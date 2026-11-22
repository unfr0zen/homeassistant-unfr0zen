"""The ZHA hot water backend's frames, through a real zigpy cluster."""

from itertools import pairwise
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest
import zigpy.types as t
from zigpy.zcl import Cluster, foundation

from custom_components.unfr0zen.hotwater import HotWater, Period, from_text
from custom_components.unfr0zen.zha_backend import ZhaHotWater

DAYS = {0: "06:00-08:00,17:00-20:00", 6: "23:00-01:00"}


def hot_water_cluster(write_status=foundation.Status.SUCCESS):
    """A zigpy cluster 0xFC01 whose unit answers reads from DAYS; each frame
    sent is kept in `frames`."""
    endpoint = MagicMock(endpoint_id=1)
    endpoint.device.ieee = t.EUI64.convert("00:12:4b:00:12:34:56:78")
    frames = []

    async def request(cluster, sequence, data, **kwargs):
        frames.append(data)
        hdr, rest = foundation.ZCLHeader.deserialize(data)
        if hdr.command_id == foundation.GeneralCommand.Read_Attributes:
            attrid = int.from_bytes(rest[:2], "little")
            value = foundation.TypeValue(type=0x42, value=t.CharacterString(DAYS.get(attrid, "")))
            record = foundation.ReadAttributeRecord(attrid=attrid, status=foundation.Status.SUCCESS, value=value)
            return foundation.GENERAL_COMMANDS[foundation.GeneralCommand.Read_Attributes_rsp].schema(
                status_records=[record]
            )
        record = foundation.WriteAttributesStatusRecord(status=write_status, attrid=0)
        return foundation.GENERAL_COMMANDS[foundation.GeneralCommand.Write_Attributes_rsp].schema(
            status_records=[record]
        )

    endpoint.request = AsyncMock(side_effect=request)
    cluster = Cluster.from_id(endpoint, 0xFC01)
    cluster.frames = frames
    return cluster


def backend(hass, cluster):
    b = ZhaHotWater(hass, "00:12:4b:00:12:34:56:78")
    return b, patch.object(ZhaHotWater, "_hot_water", return_value=cluster)


async def test_reads_one_day_per_manufacturer_request(hass):
    cluster = hot_water_cluster()
    b, p = backend(hass, cluster)
    with p:
        hw = await b.async_read()
    assert hw.days[0] == (Period(360, 480), Period(1020, 1200))
    assert hw.days[6] == (Period(1380, 60),)
    assert hw.days[1:6] == ((),) * 5
    # Manufacturer-specific (0x04), Nest Labs 0x10D6, Read Attributes, one id each.
    assert [(f[:3], f[4:]) for f in cluster.frames] == [
        (bytes([0x04, 0xD6, 0x10]), bytes([0x00, day, 0x00])) for day in range(7)
    ]


class Unit:
    """A unit's hot water cluster behind a real zigpy cluster: it holds a
    week of day strings and, as nestlocal does, takes a day only if the
    whole week then has no overlap, wrapping Sunday into Monday."""

    def __init__(self, days, refuse=None):
        self.days = dict(enumerate([""] * 7)) | days
        self.refuse = refuse
        self.written = []
        endpoint = MagicMock(endpoint_id=1)
        endpoint.device.ieee = t.EUI64.convert("00:12:4b:00:12:34:56:78")
        endpoint.request = AsyncMock(side_effect=self._request)
        self.cluster = Cluster.from_id(endpoint, 0xFC01)

    def _fits(self, days):
        spans = []
        for day, text in days.items():
            for p in from_text(text):
                start = day * 1440 + p.on
                spans.append((start, start + p.end - p.on))
        spans.sort()
        week = 7 * 1440
        return all(a[1] <= b[0] for a, b in pairwise(spans)) and (not spans or spans[-1][1] <= week + spans[0][0])

    async def _request(self, cluster, sequence, data, **kwargs):
        hdr, rest = foundation.ZCLHeader.deserialize(data)
        attrid = int.from_bytes(rest[:2], "little")
        if hdr.command_id == foundation.GeneralCommand.Read_Attributes:
            value = foundation.TypeValue(type=0x42, value=t.CharacterString(self.days[attrid]))
            record = foundation.ReadAttributeRecord(attrid=attrid, status=foundation.Status.SUCCESS, value=value)
            return foundation.GENERAL_COMMANDS[foundation.GeneralCommand.Read_Attributes_rsp].schema(
                status_records=[record]
            )
        assert (hdr.manufacturer, rest[2]) == (0x10D6, 0x42)
        text = rest[4 : 4 + rest[3]].decode()
        self.written.append((attrid, text))
        status = foundation.Status.SUCCESS
        if text == self.refuse or not self._fits(self.days | {attrid: text}):
            status = foundation.Status.INVALID_VALUE
        else:
            self.days[attrid] = text
        record = foundation.WriteAttributesStatusRecord(status=status, attrid=attrid)
        return foundation.GENERAL_COMMANDS[foundation.GeneralCommand.Write_Attributes_rsp].schema(
            status_records=[record]
        )


def week_of(unit):
    return HotWater(tuple(from_text(unit.days[d]) for d in range(7)))


async def test_writes_a_changed_day_as_a_char_string(hass):
    unit = Unit({2: "06:00-07:00"})
    b, p = backend(hass, unit.cluster)
    with p:
        await b.async_write(week_of(unit), {2: (Period(360, 480), Period(1380, 60))})
    assert unit.written == [(2, "06:00-08:00,23:00-01:00")]
    assert unit.days[2] == "06:00-08:00,23:00-01:00"


async def test_writes_the_frames_nestlocal_takes(hass):
    unit = Unit({})
    b, p = backend(hass, unit.cluster)
    frames = []
    original = unit.cluster.endpoint.request.side_effect

    async def keep(cluster, sequence, data, **kwargs):
        frames.append(data)
        return await original(cluster, sequence, data, **kwargs)

    unit.cluster.endpoint.request.side_effect = keep
    with p:
        await b.async_write(week_of(unit), {2: (Period(360, 480),)})
    text = b"06:00-08:00"
    assert [(f[:3], f[4:]) for f in frames] == [
        (bytes([0x04, 0xD6, 0x10]), bytes([0x02, 0x02, 0x00, 0x42, len(text)]) + text),
    ]


async def test_days_moving_a_period_are_cleared_then_written(hass):
    unit = Unit({0: "23:00-01:00"})
    b, p = backend(hass, unit.cluster)
    with p:
        # Monday's period past midnight moves to Tuesday's early hours.
        await b.async_write(week_of(unit), {0: (), 1: (Period(0, 60),)})
    assert unit.written == [(0, ""), (1, ""), (1, "00:00-01:00")]


async def test_an_emptied_day_writes_only_an_empty_string(hass):
    unit = Unit({6: "23:00-01:00"})
    b, p = backend(hass, unit.cluster)
    with p:
        await b.async_write(week_of(unit), {6: ()})
    assert unit.written == [(6, "")]


async def test_a_period_moved_across_midnight_is_taken(hass):
    # Tuesday 00:30-01:30 becomes Monday 23:30-01:00: written Monday first,
    # it would overlap Tuesday's old period.
    unit = Unit({1: "00:30-01:30"})
    b, p = backend(hass, unit.cluster)
    with p:
        await b.async_write(week_of(unit), {0: (Period(1410, 60),), 1: ()})
    assert unit.written == [(0, ""), (1, ""), (0, "23:30-01:00")]
    assert (unit.days[0], unit.days[1]) == ("23:30-01:00", "")


async def test_a_refused_day_is_an_error_showing_the_units_week(hass):
    unit = Unit({0: "06:00-07:00", 3: "18:00-19:00"}, refuse="06:00-08:00")
    b, p = backend(hass, unit.cluster)
    pushed = []
    with p:
        await b.async_start(pushed.append)
        with pytest.raises(HomeAssistantError, match="refused the hot water periods '06:00-08:00'"):
            await b.async_write(week_of(unit), {0: (Period(360, 480),), 3: ()})
    # Both days were cleared before Monday's value was refused, then put back.
    before = HotWater(((Period(360, 420),), (), (), (Period(1080, 1140),), (), (), ()))
    assert week_of(unit) == before
    assert pushed[-1] == before


async def test_a_refused_single_day_is_left_as_it_was(hass):
    unit = Unit({0: "06:00-07:00", 3: "18:00-19:00"}, refuse="06:00-08:00")
    b, p = backend(hass, unit.cluster)
    with p:
        await b.async_start(lambda week: None)
        with pytest.raises(HomeAssistantError, match="refused"):
            await b.async_write(week_of(unit), {0: (Period(360, 480),)})
    assert week_of(unit).days[0] == (Period(360, 420),), "never cleared"


async def test_a_reported_day_updates_the_schedule(hass):
    cluster = hot_water_cluster()
    b, p = backend(hass, cluster)
    pushed = []
    with p:
        await b.async_start(pushed.append)
        await b.async_read()
    # The unit reports Tuesday, manufacturer-specific.
    report = bytes([0x1C, 0xD6, 0x10, 0x07, 0x0A, 0x01, 0x00, 0x42, 0x0B]) + b"07:00-07:30"
    hdr, args = cluster.deserialize(report)
    cluster.handle_message(hdr, args)
    assert pushed[-1].days[1] == (Period(420, 450),)
    assert pushed[-1].days[0] == (Period(360, 480), Period(1020, 1200))


async def test_presence_is_the_cluster(hass):
    b = ZhaHotWater(hass, "00:12:4b:00:12:34:56:78")
    assert not await b.async_present(), "no ZHA, no cluster"
    with patch.object(ZhaHotWater, "_cluster", return_value=hot_water_cluster()):
        assert await b.async_present()


async def test_a_cluster_change_before_the_watch_reloads(hass):
    b = ZhaHotWater(hass, "00:12:4b:00:12:34:56:78")
    changed = MagicMock()
    # Setup decided "no hot water"; the cluster is there by the time it watches.
    with patch.object(ZhaHotWater, "_cluster", return_value=hot_water_cluster()):
        await b.async_watch(False, changed)
    b.async_stop()
    changed.assert_called_once()

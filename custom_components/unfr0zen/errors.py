"""The integration's errors, translated: each a key of the translations'
"exceptions", its placeholders filled in.

An error carries its English message, from translations/en.json, so logs
and callers read it without Home Assistant's translation cache; the
frontend shows it in the user's language from the key.
"""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN

_ENGLISH: dict[str, dict[str, str]] = json.loads(
    (Path(__file__).parent / "translations" / "en.json").read_text(encoding="utf-8")
)["exceptions"]


def _placeholders(values: dict[str, object]) -> dict[str, str]:
    return {k: str(v) for k, v in values.items()}


def _english(key: str, placeholders: dict[str, str]) -> str:
    return _ENGLISH[key]["message"].format(**placeholders)


def error(key: str, /, **placeholders: object) -> HomeAssistantError:
    """An error the Nest or a transport gave."""
    values = _placeholders(placeholders)
    return HomeAssistantError(
        _english(key, values), translation_domain=DOMAIN, translation_key=key, translation_placeholders=values
    )


def invalid(key: str, /, **placeholders: object) -> ServiceValidationError:
    """An error in what was asked."""
    values = _placeholders(placeholders)
    return ServiceValidationError(
        _english(key, values), translation_domain=DOMAIN, translation_key=key, translation_placeholders=values
    )

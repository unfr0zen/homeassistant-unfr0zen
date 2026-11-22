"""The translations: every error the code raises is in English, and every
language has the English keys and placeholders."""

import json
from pathlib import Path
import re

import pytest

PACKAGE = Path(__file__).parents[1] / "custom_components" / "unfr0zen"
TRANSLATIONS = PACKAGE / "translations"
LOCALES = PACKAGE / "frontend" / "locales"


def flat(value, prefix=""):
    """A nested translation file as {"a.b.c": text}."""
    if isinstance(value, dict):
        return {k: v for key, child in value.items() for k, v in flat(child, f"{prefix}{key}.").items()}
    return {prefix.rstrip("."): value}


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


def raised_keys():
    """The exception keys the code raises: error("key") and invalid("key"),
    with f-string keys expanded over what they're built from."""
    keys = set()
    for path in PACKAGE.glob("*.py"):
        for key in re.findall(r'\b(?:error|invalid)\(f?"([a-z0-9_{}.]+)"', path.read_text()):
            if "{self.kind}" in key:
                keys |= {key.replace("{self.kind}", kind) for kind in ("schedule", "hot_water")}
            elif "{step}" in key:
                keys |= {key.replace("{step}", step) for step in ("begin", "commit")}
            else:
                keys.add(key)
    return keys


def test_every_raised_error_has_an_english_message():
    english = json.loads((TRANSLATIONS / "en.json").read_text())["exceptions"]
    keys = raised_keys()
    assert len(keys) > 40
    assert keys <= set(english), keys - set(english)
    assert set(english) <= keys, f"unused: {set(english) - keys}"


def test_strings_json_is_the_english_translation():
    assert json.loads((PACKAGE / "strings.json").read_text()) == json.loads((TRANSLATIONS / "en.json").read_text())


@pytest.mark.parametrize(
    ("folder", "path"),
    [(TRANSLATIONS, p) for p in sorted(TRANSLATIONS.glob("*.json"))]
    + [(LOCALES, p) for p in sorted(LOCALES.glob("*.json"))],
    ids=lambda v: v.name if isinstance(v, Path) else "",
)
def test_a_language_has_the_english_keys_and_placeholders(folder, path):
    english = flat(json.loads((folder / "en.json").read_text()))
    language = flat(json.loads(path.read_text()))
    assert set(language) == set(english), set(language) ^ set(english)
    for key, text in english.items():
        assert placeholders(language[key]) == placeholders(text), key


def test_the_cards_know_every_locale_file():
    script = (PACKAGE / "frontend" / "unfr0zen-cards.js").read_text()
    listed = json.loads(re.search(r"const LANGUAGES = (\[[^\]]*\]);", script)[1])
    assert sorted(listed) == sorted(p.stem for p in LOCALES.glob("*.json"))
    assert sorted(listed) == sorted(p.stem for p in TRANSLATIONS.glob("*.json"))

"""Translation bundles stay complete and in sync (ARCHITECTURE.md §8)."""

from pathlib import Path

import pytest
import yaml

from campus.domain.errors import DomainError, all_error_classes
from campus.i18n import (
    ERROR_KEY_PREFIX,
    TranslationError,
    Translator,
    load_translator,
    translator,
)

BUNDLES = ("ru", "en")


def test_every_declared_language_has_a_bundle() -> None:
    loaded = translator()

    assert set(loaded.languages) == set(BUNDLES)


def test_bundles_define_exactly_the_same_keys() -> None:
    loaded = translator()

    keys = {lang: set(bundle) for lang, bundle in loaded.bundles.items()}

    reference = keys["ru"]
    for lang, lang_keys in keys.items():
        assert lang_keys == reference, f"{lang} differs: {lang_keys ^ reference}"


def test_every_domain_error_has_a_message() -> None:
    loaded = translator()

    missing = [
        cls.message_key
        for cls in all_error_classes()
        if not loaded.has(f"{ERROR_KEY_PREFIX}{cls.message_key}")
    ]

    assert missing == []


def test_every_error_message_belongs_to_a_domain_error() -> None:
    loaded = translator()
    known = {cls.message_key for cls in all_error_classes()}

    orphans = [
        key.removeprefix(ERROR_KEY_PREFIX)
        for key in loaded.bundles["ru"]
        if key.startswith(ERROR_KEY_PREFIX) and key.removeprefix(ERROR_KEY_PREFIX) not in known
    ]

    assert orphans == []


def test_no_message_is_empty() -> None:
    loaded = translator()

    for lang, bundle in loaded.bundles.items():
        for key, value in bundle.items():
            assert value.strip(), f"{lang}:{key} is empty"


def test_error_lookup_uses_the_requested_language() -> None:
    loaded = translator()

    assert loaded.error("en", DomainError.message_key) != loaded.error(
        "ru", DomainError.message_key
    )


def test_unknown_language_falls_back_to_the_default() -> None:
    loaded = translator()

    assert loaded.error("de", "event_not_found") == loaded.error("ru", "event_not_found")


def test_unknown_key_returns_the_key_itself() -> None:
    loaded = translator()

    assert loaded.text("ru", "nothing.here") == "nothing.here"


def test_nested_yaml_is_flattened_into_dotted_keys(tmp_path: Path) -> None:
    (tmp_path / "ru.yaml").write_text(
        yaml.safe_dump({"a": {"b": {"c": "текст"}}}, allow_unicode=True), encoding="utf-8"
    )

    loaded = load_translator(tmp_path)

    assert loaded.text("ru", "a.b.c") == "текст"


def test_placeholders_are_substituted(tmp_path: Path) -> None:
    (tmp_path / "ru.yaml").write_text("greet: 'Привет, {name}!'\n", encoding="utf-8")

    loaded = load_translator(tmp_path)

    assert loaded.text("ru", "greet", name="Аня") == "Привет, Аня!"


def test_missing_placeholder_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "ru.yaml").write_text("greet: 'Привет, {name}!'\n", encoding="utf-8")
    loaded = load_translator(tmp_path)

    with pytest.raises(TranslationError):
        loaded.text("ru", "greet", other="x")


def test_non_string_translation_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "ru.yaml").write_text("count: 3\n", encoding="utf-8")

    with pytest.raises(TranslationError):
        load_translator(tmp_path)


def test_empty_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(TranslationError):
        load_translator(tmp_path)


def test_missing_default_language_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "en.yaml").write_text("a: b\n", encoding="utf-8")

    with pytest.raises(TranslationError):
        load_translator(tmp_path, default_language="ru")


def test_translator_is_cached() -> None:
    assert translator() is translator()


def test_has_reports_known_and_unknown_keys() -> None:
    loaded: Translator = translator()

    assert loaded.has("errors.event_not_found")
    assert not loaded.has("errors.definitely_missing")

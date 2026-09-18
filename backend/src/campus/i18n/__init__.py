"""Localized text (ARCHITECTURE.md §8: no user-facing string is written in code).

Bundles are flat ``dotted.key -> text`` maps loaded from ``ru.yaml`` and ``en.yaml``. Every bundle
must define exactly the same keys; a test enforces that, so a half-translated release fails CI
rather than showing a raw key to a user.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

BUNDLE_DIR = Path(__file__).parent
ERROR_KEY_PREFIX = "errors."
DEFAULT_LANGUAGE = "ru"


class TranslationError(Exception):
    """A bundle is missing, malformed, or lacks a requested key."""


def _flatten(node: object, prefix: str = "") -> dict[str, str]:
    """YAML may nest for readability; the runtime always sees flat dotted keys."""
    if isinstance(node, str):
        return {prefix: node}
    if not isinstance(node, dict):
        msg = f"translation {prefix!r} must be a string, got {type(node).__name__}"
        raise TranslationError(msg)
    flat: dict[str, str] = {}
    for raw_key, value in node.items():  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(raw_key, str):
            msg = f"translation key {raw_key!r} must be a string"
            raise TranslationError(msg)
        flat.update(_flatten(value, f"{prefix}.{raw_key}" if prefix else raw_key))
    return flat


def _load_bundle(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        msg = f"translation bundle not found: {path}"
        raise TranslationError(msg) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        msg = f"translation bundle {path} is not valid YAML: {exc}"
        raise TranslationError(msg) from exc
    if data is None:
        return {}
    return _flatten(data)


@dataclass(frozen=True, slots=True)
class Translator:
    """Text lookup with a fallback language: a missing translation degrades, never raises."""

    bundles: dict[str, dict[str, str]]
    default_language: str = DEFAULT_LANGUAGE

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(sorted(self.bundles))

    def has(self, key: str) -> bool:
        return any(key in bundle for bundle in self.bundles.values())

    def text(self, lang: str, key: str, /, **params: Any) -> str:
        """Text for ``key``, falling back to the default language, then to the key itself."""
        for candidate in (lang, self.default_language):
            bundle = self.bundles.get(candidate)
            if bundle is not None and key in bundle:
                return _format(bundle[key], key, params)
        return key

    def error(self, lang: str, code: str, /, **params: Any) -> str:
        return self.text(lang, f"{ERROR_KEY_PREFIX}{code}", **params)


def _format(template: str, key: str, params: dict[str, Any]) -> str:
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError) as exc:
        msg = f"translation {key!r} needs a placeholder that was not provided: {exc}"
        raise TranslationError(msg) from exc


def load_translator(
    directory: Path | None = None, *, default_language: str = DEFAULT_LANGUAGE
) -> Translator:
    root = directory or BUNDLE_DIR
    bundles = {path.stem: _load_bundle(path) for path in sorted(root.glob("*.yaml"))}
    if not bundles:
        msg = f"no translation bundles found in {root}"
        raise TranslationError(msg)
    if default_language not in bundles:
        msg = f"default language {default_language!r} has no bundle in {root}"
        raise TranslationError(msg)
    return Translator(bundles=bundles, default_language=default_language)


@lru_cache(maxsize=1)
def translator() -> Translator:
    """The process-wide translator; bundles are read once."""
    return load_translator()

"""Failure-tolerant access to the sibling packages.

The web layer is the only part of Hofradar a human ever touches, so it must
boot even when ``normalize``, ``dedupe``, ``lifecycle``, ``geo``, ``scoring``,
``sources`` or ``pipeline`` are missing, half-written or raising on import.
Every cross-package call therefore goes through :func:`load`, is resolved *at
call time* rather than at module import time, and degrades into a German
notice on the page instead of a 500.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: Human-readable names for the notice text.
MODULE_LABELS = {
    "hofradar.normalize": "Normalisierung",
    "hofradar.dedupe": "Duplikaterkennung",
    "hofradar.lifecycle": "Lebenszyklus",
    "hofradar.geo": "Geokodierung",
    "hofradar.costmodel": "Kostenmodell",
    "hofradar.scoring": "Bewertung",
    "hofradar.sources": "Quellen",
    "hofradar.pipeline": "Pipeline",
    "hofradar.report": "Report",
}


class ModuleUnavailable(RuntimeError):
    """A sibling package (or one of its names) is not importable *yet*."""

    def __init__(self, target: str, original: BaseException) -> None:
        self.target = target
        self.original = original
        super().__init__(f"{target} unavailable: {original!r}")

    @property
    def module_name(self) -> str:
        return self.target.split(":", 1)[0]

    @property
    def user_message(self) -> str:
        label = MODULE_LABELS.get(self.module_name, self.module_name)
        return (
            f"Modul „{label}“ ({self.module_name}) ist noch nicht verfügbar – "
            f"{type(self.original).__name__}. Die Seite läuft im eingeschränkten Modus."
        )


@dataclass(slots=True)
class Degraded:
    """One thing the page could not do, phrased for the reader."""

    message: str
    detail: str | None = None


def load(target: str) -> Any:
    """Resolve ``"hofradar.scoring:rescore_all"`` (or a bare module path).

    Raises :class:`ModuleUnavailable` for *any* import-time problem, including a
    half-written module that raises ``AttributeError`` or ``SyntaxError`` while
    a parallel agent is saving it.
    """
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except BaseException as exc:  # noqa: BLE001 - a broken sibling must not 500 us
        raise ModuleUnavailable(target, exc) from exc
    if not attribute:
        return module
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise ModuleUnavailable(target, exc) from exc


def call(target: str, /, *args: Any, **kwargs: Any) -> Any:
    """Call a sibling function, translating every failure into ModuleUnavailable."""
    func: Callable[..., Any] = load(target)
    try:
        return func(*args, **kwargs)
    except ModuleUnavailable:
        raise
    except BaseException as exc:  # noqa: BLE001 - see module docstring
        raise ModuleUnavailable(target, exc) from exc


def call_or(target: str, fallback: Any, /, *args: Any, **kwargs: Any) -> tuple[Any, Degraded | None]:
    """Best-effort variant: ``(result, None)`` or ``(fallback, Degraded(...))``."""
    try:
        return call(target, *args, **kwargs), None
    except ModuleUnavailable as exc:
        return fallback, Degraded(exc.user_message, detail=repr(exc.original))


def is_available(module_name: str) -> bool:
    try:
        load(module_name)
    except ModuleUnavailable:
        return False
    return True

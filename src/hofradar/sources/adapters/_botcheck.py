"""Shared "are we being blocked" detection for the ToS-restricted adapters.

Deliberately does nothing about a block beyond noticing it and stopping: no
CAPTCHA solving, no proxy rotation, no header or fingerprint spoofing. If a
site's bot defences kick in, that is respected as a stop signal, not a
puzzle to route around.
"""

from __future__ import annotations

import httpx

from hofradar.sources.exceptions import BotDefenseDetected

_BLOCK_STATUSES = frozenset({403, 429, 503})

_BLOCK_MARKERS: tuple[str, ...] = (
    "captcha",
    "sind sie ein mensch",
    "bitte bestätigen sie, dass sie ein mensch",
    "access denied",
    "zugriff verweigert",
    "unusual traffic",
    "automatisierte anfragen",
    "verify you are a human",
)


def raise_if_blocked(response: httpx.Response, *, source_key: str) -> None:
    """Raise :class:`BotDefenseDetected` if this response looks like a block.

    Checked on every search and detail fetch these adapters make. Not a
    guarantee of detection - a determined anti-bot system does not have to
    announce itself - only a best-effort trip wire so a block fails cleanly
    and loudly instead of silently parsing into zero (or garbage) results.
    """
    if response.status_code in _BLOCK_STATUSES:
        raise BotDefenseDetected(
            f"{source_key}: request blocked (HTTP {response.status_code}). This adapter "
            "does not attempt to evade bot defences - if you need it, supply your own "
            "authenticated session and run it, at a low rate, from your own machine/IP."
        )
    sample = (response.text or "")[:5000].lower()
    for marker in _BLOCK_MARKERS:
        if marker in sample:
            raise BotDefenseDetected(
                f"{source_key}: response looks like a bot-defence challenge "
                f"(matched {marker!r}). Stopping rather than attempting to solve it."
            )

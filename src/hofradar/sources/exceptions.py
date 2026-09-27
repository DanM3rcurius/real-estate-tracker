"""Errors the source layer raises deliberately.

Kept separate from :mod:`hofradar.sources.base` so adapters (and tests) can
import just the exception types without pulling in the HTTP client machinery.
"""

from __future__ import annotations


class SourceError(Exception):
    """Base class for every error the source layer raises on purpose."""


class RobotsDisallowed(SourceError):
    """robots.txt forbids fetching this path and the source has respect_robots=True.

    This is a hard stop, not a warning: the polite client refuses the request
    rather than fetching it anyway.
    """


class NotSupported(SourceError):
    """The operation is not supported by this adapter's role or design.

    Raised in particular by a DISCOVERY-role adapter's ``verify()`` - a search
    snippet or aggregator entry is never proof that a listing is still live,
    so that class of source is not allowed to claim it.
    """


class SourceDiscoveryError(SourceError):
    """A source's discover() failed as a whole (not just one bad page).

    Adapters should catch and log per-item failures internally and keep
    yielding what they can; this exception is for the case where the run
    could not produce anything useful at all (site unreachable, search form
    changed shape, credentials missing) so the pipeline has something clear
    to record against the source.
    """


class BotDefenseDetected(SourceDiscoveryError):
    """A ToS-restricted, disabled-by-default adapter got blocked.

    Raised by ``kleinanzeigen``/``immoscout``/``immowelt`` when a response
    looks like a CAPTCHA/anti-bot challenge, or comes back 403/429/503. Those
    adapters never try to get around a block - this is the clean stop
    instead.
    """


class PageUnreadable(SourceError):
    """A pasted URL answered, but with nothing a listing could be read from.

    Raised by the paste-ingest adapter for a bot wall ("Ich bin kein Roboter")
    and for a page that builds its content with JavaScript and hands a plain
    fetch an empty shell. Storing either as a property is the silence that
    looks like success: an advert titled "Ich bin kein Roboter", every fact
    "k. A.". ``notice`` is the reader-facing explanation (UI copy, German),
    so the web layer can show it without knowing this class.
    """

    def __init__(self, message: str, *, notice: str) -> None:
        super().__init__(message)
        self.notice = notice

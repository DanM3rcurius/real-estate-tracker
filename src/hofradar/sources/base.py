"""The adapter contract and the shared, polite HTTP client every adapter uses.

Everything downstream trusts that a source adapter behaves like a guest, not
a nuisance: one request in flight per host at a time, spaced out by the
source's configured ``rate_limit_seconds``, identifying itself honestly, and
refusing to fetch a path robots.txt disallows. That behaviour lives here,
once, so no adapter has to reinvent it (and so none can accidentally skip
it). Adapters differ only in *where* they look and *how* they parse what
comes back - never in how considerately they ask for it.

A second contract lives here too: a source's ``role`` (see
``hofradar.db.enums.SourceRole``) decides what it is allowed to prove. Only
PRIMARY and LOCAL sources may confirm a listing is still live; a DISCOVERY
source (a search engine, an aggregator, a cache) can point at something but
can never vouch for it. ``verify()`` enforces that gate centrally so no
adapter can quietly grant itself authority it was not configured to have.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from hofradar.contracts import RawListing
from hofradar.db.enums import SourceRole
from hofradar.sources.exceptions import NotSupported, RobotsDisallowed

if TYPE_CHECKING:
    from hofradar.config import KeywordConfig, SearchProfile, SourceConfig
    from hofradar.db.models import Source

logger = logging.getLogger(__name__)

#: Sent on every request. Real, descriptive, and points somewhere a site
#: operator can find out what this is and how to reach whoever runs it.
#: Override with HOFRADAR_USER_AGENT if you deploy this under a different name.
DEFAULT_USER_AGENT = (
    "HofradarBot/0.1 (+https://github.com/hofradar/hofradar; "
    "research tool that tracks Bavarian farmstead listings for a single "
    "private buyer; low request rate; contact via repository issues)"
)

#: Phrases that mean "this listing is gone" when found on a detail page.
#: German portals rarely bother with a clean 410; the page renders 200 with
#: one of these sentences instead.
GONE_MARKERS: tuple[str, ...] = (
    "nicht mehr verfügbar",
    "anzeige wurde gelöscht",
    "das angebot wurde zurückgezogen",
    "wurde verkauft",
)

#: HTTP statuses that unambiguously mean "gone" without reading the body.
GONE_STATUSES: frozenset[int] = frozenset({404, 410})

_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def text_indicates_gone(text: str | None) -> bool:
    """True if the page body itself says the listing is no longer available."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in GONE_MARKERS)


class RateLimiter:
    """Per-host politeness: at most one request per ``interval`` seconds per host.

    Keyed by host rather than by adapter instance so that two adapters (or
    two calls within one adapter) hitting the same host still queue behind
    each other, while requests to *different* hosts never wait on one
    another.
    """

    def __init__(self, interval: float) -> None:
        self.interval = max(interval, 0.0)
        self._last_request_at: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    async def _lock_for(self, host: str) -> asyncio.Lock:
        async with self._registry_lock:
            lock = self._host_locks.get(host)
            if lock is None:
                lock = asyncio.Lock()
                self._host_locks[host] = lock
            return lock

    async def wait(self, host: str) -> None:
        if self.interval <= 0:
            return
        lock = await self._lock_for(host)
        async with lock:
            now = time.monotonic()
            last = self._last_request_at.get(host)
            if last is not None:
                remaining = self.interval - (now - last)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request_at[host] = time.monotonic()


class RobotsCache:
    """Fetches and caches robots.txt per origin, then answers can-fetch questions.

    Fails open on a fetch error (network hiccup, robots.txt missing): the
    absence of a reachable robots.txt is conventionally "no rules", not
    "forbidden". A robots.txt that was actually fetched and says no is always
    honoured.
    """

    def __init__(self, client: httpx.AsyncClient, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser] = {}
        self._lock = asyncio.Lock()

    async def _parser_for(self, origin: str) -> RobotFileParser:
        async with self._lock:
            cached = self._parsers.get(origin)
            if cached is not None:
                return cached
            parser = RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            try:
                response = await self._client.get(
                    robots_url, timeout=10.0, headers={"User-Agent": self._user_agent}
                )
                if response.status_code >= 400:
                    parser.parse([])
                else:
                    parser.parse(response.text.splitlines())
            except httpx.HTTPError as exc:
                logger.warning("robots.txt fetch failed for %s: %s - failing open", origin, exc)
                parser.parse([])
            self._parsers[origin] = parser
            return parser

    async def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = await self._parser_for(origin)
        return parser.can_fetch(self._user_agent, url)


class PoliteClient:
    """The one async HTTP client every adapter should fetch through.

    - Per-host rate limiting honouring the source's ``rate_limit_seconds``.
    - A descriptive, honest User-Agent.
    - Follows redirects.
    - Retries 429 and 5xx with exponential backoff, honouring ``Retry-After``
      when the server sends one.
    - A total per-request timeout.
    - If ``respect_robots`` is true, refuses (raises :class:`RobotsDisallowed`)
      rather than fetching a path robots.txt disallows.
    """

    def __init__(
        self,
        *,
        rate_limit_seconds: float = 2.0,
        respect_robots: bool = True,
        user_agent: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.respect_robots = respect_robots
        self.max_retries = max_retries
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": self.user_agent},
            transport=transport,
        )
        self._limiter = RateLimiter(rate_limit_seconds)
        self._robots = RobotsCache(self._client, self.user_agent)

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def get_text(self, url: str, **kwargs: Any) -> str:
        response = await self.get(url, **kwargs)
        response.raise_for_status()
        return response.text

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self.respect_robots and not await self._robots.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {method} {url}")

        host = urlparse(url).netloc
        attempt = 0
        while True:
            await self._limiter.wait(host)
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                await asyncio.sleep(self._backoff_delay(attempt))
                continue

            if response.status_code in _RETRYABLE_STATUSES:
                attempt += 1
                if attempt > self.max_retries:
                    return response
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue

            return response

    def _backoff_delay(self, attempt: int) -> float:
        return min(2.0**attempt, 30.0)

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
            try:
                dt = parsedate_to_datetime(retry_after)
                return max(dt.timestamp() - time.time(), 0.0)
            except (TypeError, ValueError):
                pass
        return self._backoff_delay(attempt)


def _adapter_options(source: Source | SourceConfig) -> dict[str, Any]:
    """Read the per-source adapter options from either config shape.

    ``SourceConfig`` (loaded straight from YAML) carries them in ``options``.
    The ``Source`` ORM row carries the whole non-columnar config blob in
    ``config``, written there by :func:`hofradar.sources.sync_sources_to_db`
    as ``{"adapter": ..., "options": {...}}``.
    """
    options = getattr(source, "options", None)
    if options is not None:
        return dict(options)
    config = getattr(source, "config", None) or {}
    return dict(config.get("options", {}))


class SourceAdapter:
    """Base class for every source. Subclasses implement discover/fetch_detail.

    Construct from either a ``Source`` ORM row (what the pipeline uses at
    runtime) or a ``SourceConfig`` (what tests and one-off scripts use) - the
    two expose the same fields under slightly different names, reconciled
    here so adapter code never has to branch on which one it got.
    """

    #: Does ``discover()`` yield a COMPLETE inventory of what this source
    #: currently offers?
    #:
    #: This is not the same question as "may this source prove things"
    #: (:attr:`can_verify`), and conflating the two is how absence detection
    #: goes wrong. Only a complete enumeration licenses the inference "this
    #: listing was not in the results, therefore it is gone".
    #:
    #: False for a source that structurally cannot enumerate: a paste box, a
    #: one-shot CSV import, a bulletin archive, or a feed that publishes only
    #: the latest N items. Such a source's silence means nothing at all.
    enumerates: bool = True

    def __init__(self, source: Source | SourceConfig, *, client: PoliteClient | None = None) -> None:
        self.key: str = source.key
        self.name: str = source.name
        self.role: str = source.role
        self.base_url: str | None = source.base_url
        self.region: str | None = getattr(source, "region", None)
        self.reliability: float = source.reliability
        self.enabled: bool = source.enabled
        self.rate_limit_seconds: float = source.rate_limit_seconds
        self.respect_robots: bool = source.respect_robots
        self.options: dict[str, Any] = _adapter_options(source)
        self._client = client
        self._owns_client = client is None
        #: Set False by an adapter whose enumeration was cut short this run -
        #: a page cap was hit, a paginator gave up, a partial result was
        #: returned. Reset at the start of every discover().
        self.enumeration_complete: bool = True
        #: URLs discover() examined this run, whether or not it chose to
        #: fetch them. A pre-filter that skips a detail fetch is a decision
        #: not to re-check something already on file, never a claim that the
        #: source stopped carrying it - so hofradar.pipeline.runner also
        #: treats a URL in this set as "still seen" for a property already on
        #: record under it, exactly as it does for a yielded RawListing.
        #: Reset at the start of every discover().
        self.enumerated_urls: set[str] = set()
        # An operator can override the class default per source, for a feed
        # they know to be exhaustive (or one they know is not).
        override = self.options.get("enumerates")
        if isinstance(override, bool):
            self.enumerates = override

    # -- shared HTTP -------------------------------------------------------- #

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient(
                rate_limit_seconds=self.rate_limit_seconds,
                respect_robots=self.respect_robots,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> SourceAdapter:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # -- enumeration ---------------------------------------------------------- #

    def begin_enumeration(self) -> None:
        """Adapters call this at the top of discover() to reset per-run state."""
        self.enumeration_complete = True
        self.enumerated_urls = set()

    def mark_enumeration_incomplete(self, reason: str) -> None:
        """Adapters call this when they know they did not see everything."""
        self.enumeration_complete = False
        logger.info("%s: enumeration incomplete - %s", self.key, reason)

    def record_enumerated_url(self, url: str) -> None:
        """Adapters call this for every item discover() examines, fetched or not.

        A pre-filtered row and a withdrawn listing must stay distinguishable:
        see :attr:`enumerated_urls`.
        """
        self.enumerated_urls.add(url)

    @property
    def can_prove_absence(self) -> bool:
        """May this run's results be read as 'everything else is gone'?"""
        return bool(self.enumerates and self.enumeration_complete and self.can_verify)

    # -- role gate ------------------------------------------------------------ #

    @property
    def can_verify(self) -> bool:
        """Only PRIMARY and LOCAL sources may confirm a listing is still live."""
        return self.role in (SourceRole.PRIMARY, SourceRole.LOCAL)

    # -- the interface -------------------------------------------------------- #

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        """Yield RawListings found for this profile/keyword vocabulary.

        Must never let a single bad page abort the run: catch and log
        per-item failures and keep going. If the run cannot produce anything
        useful at all, raise :class:`hofradar.sources.exceptions.SourceDiscoveryError`
        with a clear message rather than letting an opaque exception escape.
        """
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for typing/mypy

    async def fetch_detail(self, url: str) -> RawListing | None:
        """Fetch and parse one detail page. Returns None if it could not be read."""
        raise NotImplementedError

    async def verify(self, url: str) -> tuple[bool, int | None]:
        """Is the listing at ``url`` still live? Returns (still_live, http_status).

        Gated by ``can_verify``: a DISCOVERY adapter always raises
        :class:`NotSupported` here, because it is never allowed to prove
        availability - only PRIMARY/LOCAL sources may. Subclasses that *can*
        verify should override :meth:`_verify_impl`, not this method, so the
        gate cannot accidentally be bypassed.
        """
        if not self.can_verify:
            raise NotSupported(
                f"source {self.key!r} has role={self.role!r}; a discovery source "
                "can never confirm a listing is still live"
            )
        return await self._verify_impl(url)

    async def _verify_impl(self, url: str) -> tuple[bool, int | None]:
        """Default liveness check: GET the page, check status, scan for gone-markers.

        Good enough for most detail pages. Adapters with a distinctive "this
        is gone" signal (a redirect target, a JSON flag) should override this.
        """
        try:
            response = await self.client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("%s: verify() fetch failed for %s: %s", self.key, url, exc)
            return True, None  # unknown, not disproven - never claim "gone" on a network error
        if response.status_code in GONE_STATUSES:
            return False, response.status_code
        if response.status_code >= 400:
            return True, response.status_code
        if text_indicates_gone(response.text):
            return False, response.status_code
        return True, response.status_code

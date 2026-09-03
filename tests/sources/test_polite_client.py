"""PoliteClient: rate limiting, Retry-After, robots.txt enforcement, User-Agent."""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from hofradar.sources.base import DEFAULT_USER_AGENT, PoliteClient
from hofradar.sources.exceptions import RobotsDisallowed


@pytest.mark.asyncio
async def test_rate_limiting_spaces_out_requests_to_the_same_host():
    with respx.mock:
        respx.get("https://example.test/a").mock(return_value=httpx.Response(200, text="a"))
        respx.get("https://example.test/b").mock(return_value=httpx.Response(200, text="b"))

        client = PoliteClient(rate_limit_seconds=0.25, respect_robots=False)
        try:
            start = time.monotonic()
            await client.get("https://example.test/a")
            await client.get("https://example.test/b")
            elapsed = time.monotonic() - start
        finally:
            await client.aclose()

    assert elapsed >= 0.25, "second request to the same host must wait out the rate limit"


@pytest.mark.asyncio
async def test_rate_limiting_does_not_delay_different_hosts():
    with respx.mock:
        respx.get("https://one.test/").mock(return_value=httpx.Response(200, text="a"))
        respx.get("https://two.test/").mock(return_value=httpx.Response(200, text="b"))

        client = PoliteClient(rate_limit_seconds=5.0, respect_robots=False)
        try:
            start = time.monotonic()
            await client.get("https://one.test/")
            await client.get("https://two.test/")
            elapsed = time.monotonic() - start
        finally:
            await client.aclose()

    assert elapsed < 1.0, "requests to different hosts must not queue behind one another"


@pytest.mark.asyncio
async def test_429_with_retry_after_is_honoured(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("hofradar.sources.base.asyncio.sleep", fake_sleep)

    with respx.mock:
        route = respx.get("https://example.test/listing").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "7"}, text="slow down"),
                httpx.Response(200, text="ok now"),
            ]
        )
        client = PoliteClient(rate_limit_seconds=0.0, respect_robots=False)
        try:
            response = await client.get("https://example.test/listing")
        finally:
            await client.aclose()

    assert response.status_code == 200
    assert route.call_count == 2
    assert 7.0 in sleeps, f"expected a 7s sleep honouring Retry-After, got {sleeps}"


@pytest.mark.asyncio
async def test_5xx_is_retried_with_backoff(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("hofradar.sources.base.asyncio.sleep", fake_sleep)

    with respx.mock:
        route = respx.get("https://example.test/flaky").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, text="recovered")]
        )
        client = PoliteClient(rate_limit_seconds=0.0, respect_robots=False, max_retries=3)
        try:
            response = await client.get("https://example.test/flaky")
        finally:
            await client.aclose()

    assert response.status_code == 200
    assert route.call_count == 2
    assert len(sleeps) == 1


@pytest.mark.asyncio
async def test_robots_disallow_raises_and_never_fetches():
    with respx.mock:
        respx.get("https://example.test/robots.txt").mock(
            return_value=httpx.Response(
                200, text="User-agent: *\nDisallow: /private/\n"
            )
        )
        blocked_route = respx.get("https://example.test/private/listing").mock(
            side_effect=AssertionError("robots.txt disallows this - must never be fetched")
        )

        client = PoliteClient(rate_limit_seconds=0.0, respect_robots=True)
        try:
            with pytest.raises(RobotsDisallowed):
                await client.get("https://example.test/private/listing")
        finally:
            await client.aclose()

    assert blocked_route.call_count == 0


@pytest.mark.asyncio
async def test_robots_allowed_path_is_fetched_normally():
    with respx.mock:
        respx.get("https://example.test/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        )
        respx.get("https://example.test/public/listing").mock(
            return_value=httpx.Response(200, text="hello")
        )

        client = PoliteClient(rate_limit_seconds=0.0, respect_robots=True)
        try:
            response = await client.get("https://example.test/public/listing")
        finally:
            await client.aclose()

    assert response.status_code == 200
    assert response.text == "hello"


@pytest.mark.asyncio
async def test_missing_robots_txt_fails_open():
    with respx.mock:
        respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
        respx.get("https://example.test/page").mock(return_value=httpx.Response(200, text="ok"))

        client = PoliteClient(rate_limit_seconds=0.0, respect_robots=True)
        try:
            response = await client.get("https://example.test/page")
        finally:
            await client.aclose()

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_default_user_agent_is_sent_and_descriptive():
    assert "Hofradar" in DEFAULT_USER_AGENT
    assert "http" in DEFAULT_USER_AGENT  # points somewhere, not just a bare product token

    with respx.mock:
        route = respx.get("https://example.test/page").mock(return_value=httpx.Response(200))
        client = PoliteClient(rate_limit_seconds=0.0, respect_robots=False)
        try:
            await client.get("https://example.test/page")
        finally:
            await client.aclose()

    assert route.calls.last.request.headers["User-Agent"] == DEFAULT_USER_AGENT


@pytest.mark.asyncio
async def test_custom_user_agent_is_honoured():
    with respx.mock:
        route = respx.get("https://example.test/page").mock(return_value=httpx.Response(200))
        client = PoliteClient(
            rate_limit_seconds=0.0, respect_robots=False, user_agent="MyBot/1.0 (+https://x.test)"
        )
        try:
            await client.get("https://example.test/page")
        finally:
            await client.aclose()

    assert route.calls.last.request.headers["User-Agent"] == "MyBot/1.0 (+https://x.test)"

"""SourceAdapter role gate and the default verify() liveness check."""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.sources.adapters.manual import ManualAdapter
from hofradar.sources.adapters.web_search import WebSearchAdapter
from hofradar.sources.exceptions import NotSupported


@pytest.mark.asyncio
async def test_discovery_adapter_verify_raises_not_supported(make_source_config):
    cfg = make_source_config(key="web_search", adapter="web_search", role="discovery")
    adapter = WebSearchAdapter(cfg)

    assert adapter.can_verify is False
    with pytest.raises(NotSupported):
        await adapter.verify("https://example.test/some-listing")


@pytest.mark.asyncio
async def test_primary_adapter_can_verify_is_true(make_source_config):
    cfg = make_source_config(key="manual", adapter="manual", role="primary")
    adapter = ManualAdapter(cfg)
    assert adapter.can_verify is True


@pytest.mark.asyncio
async def test_verify_returns_false_for_404(make_source_config):
    cfg = make_source_config(key="manual", adapter="manual", role="primary")
    adapter = ManualAdapter(cfg)

    with respx.mock:
        respx.get("https://example.test/gone").mock(return_value=httpx.Response(404))
        live, status = await adapter.verify("https://example.test/gone")

    assert live is False
    assert status == 404


@pytest.mark.asyncio
async def test_verify_returns_false_for_gone_marker_text(make_source_config, read_fixture):
    cfg = make_source_config(key="manual", adapter="manual", role="primary")
    adapter = ManualAdapter(cfg)
    gone_html = read_fixture("detail_gone.html")

    with respx.mock:
        respx.get("https://example.test/removed").mock(return_value=httpx.Response(200, text=gone_html))
        live, status = await adapter.verify("https://example.test/removed")

    assert live is False
    assert status == 200


@pytest.mark.asyncio
async def test_verify_returns_true_for_a_live_page(make_source_config, read_fixture):
    cfg = make_source_config(key="manual", adapter="manual", role="primary")
    adapter = ManualAdapter(cfg)
    live_html = read_fixture("detail_live.html")

    with respx.mock:
        respx.get("https://example.test/live").mock(return_value=httpx.Response(200, text=live_html))
        live, status = await adapter.verify("https://example.test/live")

    assert live is True
    assert status == 200

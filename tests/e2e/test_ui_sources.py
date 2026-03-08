"""E2E tests for the sources panel — verifies limit=0 surfaces all sources.

Catches regressions from the previous limit=100 cap that hid sources in
large knowledge bases. Includes both API-level (httpx) and browser-level
(Playwright) coverage.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = [pytest.mark.e2e]


class TestSourcesPanelUnlimited:
    """Verify the sources API returns all sources when limit=0."""

    def test_sources_api_limit_zero_returns_all(self, api_client: httpx.Client) -> None:
        """GET /api/sources?limit=0 returns more than 100 sources when they exist."""
        source_ids: list[str] = []
        for i in range(110):
            resp = api_client.post(
                "/api/sources",
                json={"type": "text", "title": f"Source {i:03d}", "content": f"Content {i}"},
            )
            resp.raise_for_status()
            source_ids.append(resp.json()["id"])

        resp = api_client.get("/api/sources?limit=0")
        resp.raise_for_status()
        data = resp.json()

        sources = data["sources"]
        assert len(sources) >= 110, f"Expected >=110 sources, got {len(sources)}"

        returned_ids = {s["id"] for s in sources}
        assert source_ids[-1] in returned_ids, "Last source should be in unlimited results"

        for sid in source_ids:
            api_client.delete(f"/api/sources/{sid}")

    def test_sources_api_limit_100_caps(self, api_client: httpx.Client) -> None:
        """GET /api/sources?limit=100 caps at 100 (old behavior for comparison)."""
        source_ids: list[str] = []
        for i in range(105):
            resp = api_client.post(
                "/api/sources",
                json={"type": "text", "title": f"Cap Test {i:03d}", "content": f"Cap {i}"},
            )
            resp.raise_for_status()
            source_ids.append(resp.json()["id"])

        resp = api_client.get("/api/sources?limit=100")
        resp.raise_for_status()
        data = resp.json()

        assert len(data["sources"]) == 100, f"Expected exactly 100, got {len(data['sources'])}"

        for sid in source_ids:
            api_client.delete(f"/api/sources/{sid}")


# ---------------------------------------------------------------------------
# Playwright browser tests (skip if Playwright unavailable)
# ---------------------------------------------------------------------------

try:
    from playwright.sync_api import Page, expect  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

requires_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")


@requires_playwright
class TestBrowserSourcesPanelUnlimited:
    """Verify the browser sources panel renders >100 sources via Playwright."""

    def test_sources_panel_shows_beyond_100(self, page: "Page", base_url: str, api_client: httpx.Client) -> None:
        """Seed >100 sources, open the sources panel, verify all are rendered."""
        # Seed 105 sources via API
        source_ids: list[str] = []
        for i in range(105):
            resp = api_client.post(
                "/api/sources",
                json={"type": "text", "title": f"BrowserSrc {i:03d}", "content": f"Content {i}"},
            )
            resp.raise_for_status()
            source_ids.append(resp.json()["id"])

        # Navigate and open sources panel
        page.goto(base_url)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector("#btn-send", timeout=10000)

        toggle = page.locator("#btn-toggle-sources")
        if not toggle.is_visible():
            pytest.skip("Sources toggle button not in UI")

        toggle.click()
        page.wait_for_timeout(1500)

        # Count rendered source items in the panel
        items = page.locator("#sources-list .source-item, #sources-list .sources-item, #sources-list li")
        count = items.count()

        # Should have at least 105 items — the old limit=100 would have capped this
        assert count >= 105, f"Sources panel shows {count} items, expected >=105 (limit=0 uncapped)"

        # Clean up
        for sid in source_ids:
            api_client.delete(f"/api/sources/{sid}")

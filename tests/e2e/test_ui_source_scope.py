"""E2E tests for source scope feedback (#853).

Verifies that out-of-scope source metadata propagates correctly through
the API endpoints used by the web UI's sources panel and chat SSE stream.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = [pytest.mark.e2e]


class TestSpaceSourcesTagEnrichment:
    """Verify /api/spaces/{id}/sources includes tag_ids for scope computation."""

    def _create_space(self, api_client: httpx.Client) -> str:
        resp = api_client.post("/api/spaces", json={"name": "scope-test"})
        resp.raise_for_status()
        return resp.json()["id"]

    def _create_source_with_tag(self, api_client: httpx.Client, title: str, tag_name: str) -> tuple[str, str]:
        """Create a source and a tag, link them. Returns (source_id, tag_id)."""
        resp = api_client.post(
            "/api/sources",
            json={"type": "text", "title": title, "content": f"Content for {title}"},
        )
        resp.raise_for_status()
        source_id = resp.json()["id"]

        resp = api_client.post("/api/tags", json={"name": tag_name})
        resp.raise_for_status()
        tag_id = resp.json()["id"]

        resp = api_client.post(f"/api/sources/{source_id}/tags/{tag_id}")
        resp.raise_for_status()

        return source_id, tag_id

    def test_space_sources_include_tag_ids(self, api_client: httpx.Client) -> None:
        """GET /api/spaces/{id}/sources should include tag_ids on each source."""
        space_id = self._create_space(api_client)

        source_id, tag_id = self._create_source_with_tag(api_client, "Scope Doc", "finance")

        # Link source to space
        api_client.post(f"/api/spaces/{space_id}/sources", json={"source_id": source_id})

        resp = api_client.get(f"/api/spaces/{space_id}/sources")
        resp.raise_for_status()
        sources = resp.json()

        matched = [s for s in sources if s["id"] == source_id]
        assert len(matched) == 1, f"Expected source in space, got {len(matched)}"
        assert "tag_ids" in matched[0], "tag_ids field missing from space sources response"
        assert tag_id in matched[0]["tag_ids"], f"Expected tag {tag_id} in tag_ids"

        # Cleanup
        api_client.delete(f"/api/spaces/{space_id}/sources/{source_id}")
        api_client.delete(f"/api/spaces/{space_id}")
        api_client.delete(f"/api/sources/{source_id}")

    def test_global_sources_include_tag_ids(self, api_client: httpx.Client) -> None:
        """GET /api/sources should also include tag_ids (parity check)."""
        resp = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Global Tag Test", "content": "content"},
        )
        resp.raise_for_status()
        source_id = resp.json()["id"]

        resp = api_client.post("/api/tags", json={"name": "global-tag"})
        resp.raise_for_status()
        tag_id = resp.json()["id"]

        api_client.post(f"/api/sources/{source_id}/tags/{tag_id}")

        resp = api_client.get("/api/sources")
        resp.raise_for_status()
        sources = resp.json()["sources"]

        matched = [s for s in sources if s["id"] == source_id]
        assert len(matched) == 1
        assert tag_id in matched[0].get("tag_ids", [])

        api_client.delete(f"/api/sources/{source_id}")

    def test_space_sources_tag_ids_empty_when_no_tags(self, api_client: httpx.Client) -> None:
        """Sources without tags should have tag_ids: [] (not missing)."""
        space_id = self._create_space(api_client)

        resp = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "No Tags", "content": "plain"},
        )
        resp.raise_for_status()
        source_id = resp.json()["id"]

        api_client.post(f"/api/spaces/{space_id}/sources", json={"source_id": source_id})

        resp = api_client.get(f"/api/spaces/{space_id}/sources")
        resp.raise_for_status()
        sources = resp.json()

        matched = [s for s in sources if s["id"] == source_id]
        assert len(matched) == 1
        assert matched[0]["tag_ids"] == []

        api_client.delete(f"/api/spaces/{space_id}/sources/{source_id}")
        api_client.delete(f"/api/spaces/{space_id}")
        api_client.delete(f"/api/sources/{source_id}")

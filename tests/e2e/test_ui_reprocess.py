"""E2E tests for source reprocess and embedding_status features.

Verifies the POST /sources/{id}/reprocess endpoint and the embedding_status
field presence on source list and detail responses.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = [pytest.mark.e2e]


class TestSourceReprocess:
    """Verify POST /api/sources/{id}/reprocess behavior."""

    def test_reprocess_returns_source_with_warnings(self, api_client: httpx.Client) -> None:
        """POST /api/sources/{id}/reprocess returns 200 with source dict and warnings list."""
        resp = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Reprocess Test", "content": "Hello world"},
        )
        resp.raise_for_status()
        source_id = resp.json()["id"]

        try:
            resp = api_client.post(f"/api/sources/{source_id}/reprocess")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

            data = resp.json()
            assert "id" in data, "Response should contain source id"
            assert data["id"] == source_id
            assert "warnings" in data, "Response should contain warnings list"
            assert isinstance(data["warnings"], list)
        finally:
            api_client.delete(f"/api/sources/{source_id}")

    def test_reprocess_nonexistent_source_returns_404(self, api_client: httpx.Client) -> None:
        """POST /api/sources/{fake-uuid}/reprocess returns 404 for missing sources."""
        fake_id = str(uuid.uuid4())
        resp = api_client.post(f"/api/sources/{fake_id}/reprocess")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


class TestEmbeddingStatus:
    """Verify embedding_status field is present on source responses."""

    def test_embedding_status_in_source_list(self, api_client: httpx.Client) -> None:
        """GET /api/sources includes embedding_status on each source."""
        resp = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "EmbStatus List", "content": "Status test"},
        )
        resp.raise_for_status()
        source_id = resp.json()["id"]

        try:
            resp = api_client.get("/api/sources")
            resp.raise_for_status()
            data = resp.json()

            sources = data["sources"]
            assert len(sources) > 0, "Expected at least one source"

            matching = [s for s in sources if s["id"] == source_id]
            assert len(matching) == 1, "Created source should appear in list"
            assert "embedding_status" in matching[0], "Source should have embedding_status field"
        finally:
            api_client.delete(f"/api/sources/{source_id}")

    def test_embedding_status_in_source_detail(self, api_client: httpx.Client) -> None:
        """GET /api/sources/{id} includes embedding_status field."""
        resp = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "EmbStatus Detail", "content": "Detail test"},
        )
        resp.raise_for_status()
        source_id = resp.json()["id"]

        try:
            resp = api_client.get(f"/api/sources/{source_id}")
            resp.raise_for_status()
            data = resp.json()

            assert data["id"] == source_id
            assert "embedding_status" in data, "Source detail should have embedding_status field"
        finally:
            api_client.delete(f"/api/sources/{source_id}")

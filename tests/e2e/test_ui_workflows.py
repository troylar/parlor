"""E2E tests for workflow REST API endpoints.

Tests the real server (no mocking of middleware or routing).
Verifies read-only workflow monitoring API and SSE endpoint.
No browser UI pages tested — this issue only delivers the API layer.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]


class TestWorkflowAPIEndpoints:
    """Verify workflow REST API returns correct responses."""

    def test_list_workflows(self, api_client) -> None:
        """GET /api/workflows returns a list."""
        resp = api_client.get("/api/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_list_workflow_runs_empty(self, api_client) -> None:
        """GET /api/workflow-runs returns empty list when no runs exist."""
        resp = api_client.get("/api/workflow-runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_workflow_run_404(self, api_client) -> None:
        """GET /api/workflow-runs/{id} returns 404 for nonexistent run."""
        resp = api_client.get("/api/workflow-runs/nonexistent-id")
        assert resp.status_code == 404

    def test_get_workflow_events_404(self, api_client) -> None:
        """GET /api/workflow-runs/{id}/events returns 404 for nonexistent run."""
        resp = api_client.get("/api/workflow-runs/nonexistent-id/events")
        assert resp.status_code == 404

    def test_sse_endpoint_accepts_workflow_run_id(self, api_client) -> None:
        """GET /api/events?workflow_run_id=... starts an SSE stream."""
        with api_client.stream(
            "GET",
            "/api/events",
            params={"workflow_run_id": "test-run-id"},
            timeout=5,
        ) as resp:
            assert resp.status_code == 200
            content_type = resp.headers.get("content-type", "")
            assert "text/event-stream" in content_type

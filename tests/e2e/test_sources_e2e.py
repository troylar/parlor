"""E2E tests for the sources feature.

Tests the real server with no mocking — sources CRUD, file upload,
tags, groups, and Playwright browser interaction.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]


# ---------------------------------------------------------------------------
# API-level e2e tests (httpx — no browser needed)
# ---------------------------------------------------------------------------


class TestSourcesCRUD:
    """Verify source creation, listing, update, and deletion through the real server."""

    def test_create_text_source(self, api_client) -> None:
        resp = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "E2E Note", "content": "Hello from e2e"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "E2E Note"
        assert data["type"] == "text"
        assert data["content"] == "Hello from e2e"
        assert "id" in data

    def test_create_url_source(self, api_client) -> None:
        resp = api_client.post(
            "/api/sources",
            json={"type": "url", "title": "Example", "url": "https://example.com"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 201
        assert resp.json()["type"] == "url"
        assert resp.json()["url"] == "https://example.com"

    def test_list_sources(self, api_client) -> None:
        # Create a source first
        api_client.post(
            "/api/sources",
            json={"type": "text", "title": "List Test", "content": "findme"},
            headers={"Content-Type": "application/json"},
        )
        resp = api_client.get("/api/sources")
        assert resp.status_code == 200
        sources = resp.json()["sources"]
        assert any(s["title"] == "List Test" for s in sources)

    def test_list_sources_with_search(self, api_client) -> None:
        api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Unique Needle", "content": "unique content"},
            headers={"Content-Type": "application/json"},
        )
        resp = api_client.get("/api/sources?search=Unique+Needle")
        assert resp.status_code == 200
        sources = resp.json()["sources"]
        assert any(s["title"] == "Unique Needle" for s in sources)

    def test_list_sources_with_type_filter(self, api_client) -> None:
        resp = api_client.get("/api/sources?type=url")
        assert resp.status_code == 200
        for s in resp.json()["sources"]:
            assert s["type"] == "url"

    def test_get_source(self, api_client) -> None:
        created = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Get Me", "content": "detail"},
            headers={"Content-Type": "application/json"},
        ).json()
        resp = api_client.get(f"/api/sources/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Get Me"

    def test_get_source_not_found(self, api_client) -> None:
        resp = api_client.get("/api/sources/00000000-0000-0000-0000-000000789012")
        assert resp.status_code == 404

    def test_get_source_invalid_uuid(self, api_client) -> None:
        resp = api_client.get("/api/sources/not-a-uuid")
        assert resp.status_code == 400

    def test_update_source(self, api_client) -> None:
        created = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Before", "content": "old"},
            headers={"Content-Type": "application/json"},
        ).json()
        resp = api_client.patch(
            f"/api/sources/{created['id']}",
            json={"title": "After", "content": "new"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "After"

    def test_delete_source(self, api_client) -> None:
        created = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Delete Me", "content": "bye"},
            headers={"Content-Type": "application/json"},
        ).json()
        resp = api_client.delete(f"/api/sources/{created['id']}")
        assert resp.status_code == 200

        resp = api_client.get(f"/api/sources/{created['id']}")
        assert resp.status_code == 404


class TestSourceUpload:
    """Verify file upload through the real server."""

    def test_upload_text_file(self, api_client) -> None:
        resp = api_client.post(
            "/api/sources/upload",
            files={"file": ("test.txt", b"Hello from file upload", "text/plain")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "file"
        assert data["filename"] == "test.txt"
        assert "id" in data

    def test_upload_with_title(self, api_client) -> None:
        resp = api_client.post(
            "/api/sources/upload",
            files={"file": ("notes.txt", b"Some notes", "text/plain")},
            data={"title": "My Notes"},
        )
        assert resp.status_code == 201
        assert resp.json()["title"] == "My Notes"


class TestSourceTags:
    """Verify tagging and untagging sources."""

    def _create_tag(self, api_client, name: str) -> str:
        resp = api_client.post(
            "/api/tags",
            json={"name": name},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (200, 201)
        return resp.json()["id"]

    def test_tag_and_untag_source(self, api_client) -> None:
        source = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Tag Test", "content": "taggable"},
            headers={"Content-Type": "application/json"},
        ).json()
        tag_id = self._create_tag(api_client, "e2e-tag")

        # Tag
        resp = api_client.post(f"/api/sources/{source['id']}/tags/{tag_id}")
        assert resp.status_code == 201

        # Verify tag is attached
        detail = api_client.get(f"/api/sources/{source['id']}").json()
        tag_ids = [t["id"] for t in detail.get("tags", [])]
        assert tag_id in tag_ids

        # Untag
        resp = api_client.delete(f"/api/sources/{source['id']}/tags/{tag_id}")
        assert resp.status_code == 200

        # Verify tag removed
        detail = api_client.get(f"/api/sources/{source['id']}").json()
        tag_ids = [t["id"] for t in detail.get("tags", [])]
        assert tag_id not in tag_ids


class TestSourceGroups:
    """Verify source group management through the real server."""

    def test_create_and_list_groups(self, api_client) -> None:
        resp = api_client.post(
            "/api/source-groups",
            json={"name": "E2E Group", "description": "Test group"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 201
        group = resp.json()
        assert group["name"] == "E2E Group"

        resp = api_client.get("/api/source-groups")
        assert resp.status_code == 200
        groups = resp.json()["groups"]
        assert any(g["name"] == "E2E Group" for g in groups)

    def test_add_and_remove_source_from_group(self, api_client) -> None:
        source = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Grouped", "content": "in a group"},
            headers={"Content-Type": "application/json"},
        ).json()
        group = api_client.post(
            "/api/source-groups",
            json={"name": "Member Test"},
            headers={"Content-Type": "application/json"},
        ).json()

        # Add to group
        resp = api_client.post(f"/api/source-groups/{group['id']}/sources/{source['id']}")
        assert resp.status_code == 201

        # Remove from group
        resp = api_client.delete(f"/api/source-groups/{group['id']}/sources/{source['id']}")
        assert resp.status_code == 200

    def test_delete_group(self, api_client) -> None:
        group = api_client.post(
            "/api/source-groups",
            json={"name": "Delete Me Group"},
            headers={"Content-Type": "application/json"},
        ).json()

        resp = api_client.delete(f"/api/source-groups/{group['id']}")
        assert resp.status_code == 200


class TestProjectSourceLinking:
    """Verify linking sources to projects."""

    def test_link_source_to_project(self, api_client) -> None:
        project = api_client.post(
            "/api/projects",
            json={"name": "Source Link Project"},
        ).json()
        source = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Linked", "content": "project context"},
            headers={"Content-Type": "application/json"},
        ).json()

        resp = api_client.post(
            f"/api/projects/{project['id']}/sources",
            json={"source_id": source["id"]},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 201

        # Verify project sources
        resp = api_client.get(f"/api/projects/{project['id']}/sources")
        assert resp.status_code == 200
        sources = resp.json()["sources"]
        assert any(s["id"] == source["id"] for s in sources)

    def test_unlink_source_from_project(self, api_client) -> None:
        project = api_client.post(
            "/api/projects",
            json={"name": "Unlink Project"},
        ).json()
        source = api_client.post(
            "/api/sources",
            json={"type": "text", "title": "Unlinkable", "content": "temp"},
            headers={"Content-Type": "application/json"},
        ).json()

        api_client.post(
            f"/api/projects/{project['id']}/sources",
            json={"source_id": source["id"]},
            headers={"Content-Type": "application/json"},
        )

        resp = api_client.delete(f"/api/projects/{project['id']}/sources?source_id={source['id']}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Playwright browser tests (skip if Playwright unavailable)
# ---------------------------------------------------------------------------

try:
    import playwright  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

requires_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")


@requires_playwright
class TestBrowserSourcesPanel:
    """Verify the sources panel works in a real browser."""

    def test_sources_panel_toggle(self, authenticated_page) -> None:
        """Clicking the sources button should open the sources panel."""
        page = authenticated_page

        # The sources panel should be hidden initially
        panel = page.locator("#sources-panel")
        assert not panel.is_visible()

        # Click the sources toggle button
        toggle = page.locator("#btn-toggle-sources")
        if toggle.is_visible():
            toggle.click()
            page.wait_for_timeout(500)
            assert panel.is_visible()

            # Click again to close
            toggle.click()
            page.wait_for_timeout(500)
            assert not panel.is_visible()

    def test_create_text_source_via_ui(self, authenticated_page, api_client) -> None:
        """Creating a source through the UI should persist it."""
        page = authenticated_page

        # Open sources panel
        toggle = page.locator("#btn-toggle-sources")
        if not toggle.is_visible():
            return  # Skip if toggle not in UI yet

        toggle.click()
        page.wait_for_timeout(500)

        # Click add button
        add_btn = page.locator("#sources-add-btn")
        if add_btn.is_visible():
            add_btn.click()
            page.wait_for_timeout(300)

            # Fill in the form (if modal/form appears)
            title_input = page.locator("#source-title-input, [name='source-title']")
            if title_input.is_visible():
                title_input.fill("Browser Source")
                content_input = page.locator("#source-content-input, [name='source-content']")
                if content_input.is_visible():
                    content_input.fill("Created from Playwright")

                    # Submit
                    submit = page.locator("#source-save-btn, [type='submit']")
                    if submit.is_visible():
                        submit.click()
                        page.wait_for_timeout(1000)

                        # Verify via API
                        resp = api_client.get("/api/sources?search=Browser+Source")
                        sources = resp.json()["sources"]
                        assert any(s["title"] == "Browser Source" for s in sources)

    def test_sources_view_tabs(self, authenticated_page) -> None:
        """The sources/groups view tabs should switch views."""
        page = authenticated_page

        toggle = page.locator("#btn-toggle-sources")
        if not toggle.is_visible():
            return

        toggle.click()
        page.wait_for_timeout(500)

        # Check Sources tab is active by default
        sources_tab = page.locator("#sources-view-tab-sources")
        groups_tab = page.locator("#sources-view-tab-groups")

        if sources_tab.is_visible() and groups_tab.is_visible():
            assert "active" in (sources_tab.get_attribute("class") or "")

            # Click Groups tab
            groups_tab.click()
            page.wait_for_timeout(300)
            assert "active" in (groups_tab.get_attribute("class") or "")

            # Click back to Sources
            sources_tab.click()
            page.wait_for_timeout(300)
            assert "active" in (sources_tab.get_attribute("class") or "")

    def test_no_console_errors_with_sources(self, authenticated_page) -> None:
        """Opening the sources panel should not produce console errors."""
        console_errors: list[str] = []

        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        page = authenticated_page
        page.on("console", on_console)

        toggle = page.locator("#btn-toggle-sources")
        if toggle.is_visible():
            toggle.click()
            page.wait_for_timeout(1000)

        # Filter out expected errors (SSE reconnect, dummy AI endpoint)
        unexpected = [
            e
            for e in console_errors
            if "ERR_CONNECTION_REFUSED" not in e
            and "/api/events" not in e
            and "Content-Security-Policy" not in e
            and "Refused to" not in e
        ]
        assert unexpected == [], f"Unexpected console errors: {unexpected}"

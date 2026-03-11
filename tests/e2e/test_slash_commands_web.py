"""Playwright coverage for web slash-command UX."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml

from anteroom.db import get_db
from anteroom.services.artifact_storage import create_artifact
from anteroom.services.pack_attachments import attach_pack
from anteroom.services.packs import install_pack, parse_manifest
from anteroom.services.storage import create_message

try:
    import playwright  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

requires_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")

pytestmark = [pytest.mark.e2e]


def _e2e_db(app_server: tuple[str, Path]):
    return get_db(app_server[1] / "chat.db")


def _create_e2e_pack_dir(base: Path, *, name: str, namespace: str) -> Path:
    pack_dir = base / f"{namespace}-{name}"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "skills").mkdir(exist_ok=True)
    (pack_dir / "skills" / "hello.yaml").write_text(
        "content: Hello from browser e2e\nmetadata:\n  tier: read\n",
        encoding="utf-8",
    )
    with open(pack_dir / "pack.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "name": name,
                "namespace": namespace,
                "version": "1.0.0",
                "description": "Browser test pack",
                "artifacts": [{"type": "skill", "name": "hello"}],
            },
            f,
        )
    return pack_dir


def _install_chat_stream_mock(page, calls: list[list[dict]]) -> None:
    page.evaluate(
        """(calls) => {
            if (!window.__anteroomOrigFetchForE2E) {
                window.__anteroomOrigFetchForE2E = window.fetch.bind(window);
            }
            const encoder = new TextEncoder();
            let callIndex = 0;
            window.fetch = (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                if (!url.includes('/chat')) {
                    return window.__anteroomOrigFetchForE2E(input, init);
                }
                const plan = calls[Math.min(callIndex, calls.length - 1)] || [];
                callIndex += 1;
                const stream = new ReadableStream({
                    start(controller) {
                        (async () => {
                            for (const step of plan) {
                                if (step.delayMs) {
                                    await new Promise(resolve => setTimeout(resolve, step.delayMs));
                                }
                                if (step.close) {
                                    controller.close();
                                    return;
                                }
                                const payload =
                                    `event: ${step.event}\\n` +
                                    `data: ${JSON.stringify(step.data || {})}\\n\\n`;
                                controller.enqueue(encoder.encode(payload));
                            }
                            controller.close();
                        })().catch(error => controller.error(error));
                    },
                });
                return Promise.resolve(
                    new Response(stream, {
                        status: 200,
                        headers: { 'Content-Type': 'text/event-stream' },
                    })
                );
            };
        }""",
        calls,
    )


def _restore_chat_stream_mock(page) -> None:
    page.evaluate(
        """() => {
            if (window.__anteroomOrigFetchForE2E) {
                window.fetch = window.__anteroomOrigFetchForE2E;
                delete window.__anteroomOrigFetchForE2E;
            }
        }"""
    )


def _install_app_api_mock(page) -> None:
    page.evaluate(
        """() => {
            if (!window.__anteroomOrigAppApiForE2E) {
                window.__anteroomOrigAppApiForE2E = App.api.bind(App);
            }
            window.__anteroomApiCallsForE2E = [];
            App.api = async (url, options = {}) => {
                window.__anteroomApiCallsForE2E.push({
                    url,
                    method: options.method || 'GET',
                    body: options.body || '',
                });
                return { ok: true };
            };
        }"""
    )


def _restore_app_api_mock(page) -> None:
    page.evaluate(
        """() => {
            if (window.__anteroomOrigAppApiForE2E) {
                App.api = window.__anteroomOrigAppApiForE2E;
                delete window.__anteroomOrigAppApiForE2E;
            }
            delete window.__anteroomApiCallsForE2E;
        }"""
    )


@requires_playwright
class TestWebSlashCommands:
    def test_approval_prompt_allow_session_updates_card_and_calls_api(self, authenticated_page) -> None:
        page = authenticated_page
        _install_app_api_mock(page)
        try:
            page.evaluate(
                """() => {
                    Chat.showApprovalPrompt({
                        approval_id: 'approval-e2e-1',
                        reason: 'Need approval to run a shell command.',
                        details: { command: 'rm -rf /tmp/demo' },
                    });
                }"""
            )

            page.locator('.approval-prompt[data-approval-id="approval-e2e-1"] .approval-session').click()
            page.wait_for_function(
                """
                () => {
                    const el = document.querySelector(
                        '.approval-prompt[data-approval-id="approval-e2e-1"] .approval-status'
                    );
                    return !!el && el.textContent.includes('Allowed for Session');
                }
                """,
                timeout=5000,
            )
            calls = page.evaluate("() => window.__anteroomApiCallsForE2E")
        finally:
            _restore_app_api_mock(page)

        assert calls == [{
            "url": "/api/approvals/approval-e2e-1/respond",
            "method": "POST",
            "body": '{"approved":true,"scope":"session"}',
        }]

    def test_ask_user_option_updates_card_and_calls_api(self, authenticated_page) -> None:
        page = authenticated_page
        _install_app_api_mock(page)
        try:
            page.evaluate(
                """() => {
                    Chat.showAskUserPrompt({
                        ask_id: 'ask-e2e-1',
                        question: 'Choose a deploy target',
                        options: ['staging', 'production'],
                    });
                }"""
            )

            page.locator('.ask-user-prompt[data-ask-id="ask-e2e-1"] .ask-user-option:has-text("staging")').click()
            page.wait_for_function(
                """
                () => {
                    const el = document.querySelector('.ask-user-prompt[data-ask-id="ask-e2e-1"] .ask-user-status');
                    return !!el && el.textContent.includes('staging');
                }
                """,
                timeout=5000,
            )
            calls = page.evaluate("() => window.__anteroomApiCallsForE2E")
        finally:
            _restore_app_api_mock(page)

        assert calls == [{
            "url": "/api/approvals/ask-e2e-1/respond",
            "method": "POST",
            "body": '{"approved":true,"answer":"staging"}',
        }]

    def test_ask_user_freeform_cancel_updates_card_and_calls_api(self, authenticated_page) -> None:
        page = authenticated_page
        _install_app_api_mock(page)
        try:
            page.evaluate(
                """() => {
                    Chat.showAskUserPrompt({
                        ask_id: 'ask-e2e-2',
                        question: 'Describe the rollout plan',
                    });
                }"""
            )

            page.locator('.ask-user-prompt[data-ask-id="ask-e2e-2"] .ask-user-cancel').click()
            page.wait_for_function(
                """
                () => {
                    const el = document.querySelector('.ask-user-prompt[data-ask-id="ask-e2e-2"] .ask-user-status');
                    return !!el && el.textContent.includes('Cancelled');
                }
                """,
                timeout=5000,
            )
            calls = page.evaluate("() => window.__anteroomApiCallsForE2E")
        finally:
            _restore_app_api_mock(page)

        assert calls == [{
            "url": "/api/approvals/ask-e2e-2/respond",
            "method": "POST",
            "body": '{"approved":false,"answer":""}',
        }]

    def test_retrying_phase_is_visible_during_stream_recovery(self, authenticated_page, api_client) -> None:
        conv = api_client.post("/api/conversations", json={"title": f"Retry Phase {uuid.uuid4().hex[:8]}"}).json()
        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)

        _install_chat_stream_mock(
            page,
            [[
                {"event": "retrying", "data": {"attempt": 2, "max_attempts": 3}},
                {"delayMs": 300, "event": "token", "data": {"content": "Recovered "}},
                {"event": "done", "data": {}},
            ]],
        )
        try:
            page.locator("#message-input").fill("Trigger retry state")
            page.locator("#message-input").press("Enter")
            page.wait_for_function(
                "() => (document.getElementById('thinking-phase')?.textContent || '').includes('retry 2/3')",
                timeout=10000,
            )
            page.wait_for_function(
                "() => document.querySelector('#messages-container')?.textContent?.includes('Recovered')",
                timeout=10000,
            )
        finally:
            _restore_chat_stream_mock(page)

        transcript = page.locator("#messages-container").text_content() or ""
        assert "Recovered" in transcript

    def test_list_command_opens_picker_and_supports_filter_and_keyboard_nav(
        self,
        authenticated_page,
        api_client,
    ) -> None:
        suffix = uuid.uuid4().hex[:8]
        titles = [f"Slash Alpha {suffix}", f"Slash Beta {suffix}", f"Slash Gamma {suffix}"]
        for title in titles:
            resp = api_client.post("/api/conversations", json={"title": title})
            resp.raise_for_status()

        page = authenticated_page
        page.locator("#message-input").fill("/list 3")
        page.locator("#message-input").press("Enter")

        picker = page.locator(".command-picker-overlay")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)
        expect_title = page.locator(".command-picker-title")
        assert expect_title.text_content() == "Conversation Results"
        assert picker.locator(".command-item").count() >= 3

        picker.locator(".command-picker-input").fill(f"Beta {suffix}")
        page.wait_for_timeout(200)
        assert picker.locator(".command-item").count() == 1
        assert picker.locator(".command-picker-meta").text_content() == f'1 result for "Beta {suffix}"'

        page.keyboard.press("ArrowDown")
        active = picker.locator(".command-item.active")
        assert active.count() == 1
        assert f"Beta {suffix}" in (active.locator(".command-item-title").text_content() or "")
        assert "Enter Open" in (picker.locator(".command-picker-footer").text_content() or "")

    def test_list_picker_enter_opens_selected_conversation(self, authenticated_page, api_client) -> None:
        suffix = uuid.uuid4().hex[:8]
        api_client.post("/api/conversations", json={"title": f"Picker Alpha {suffix}"}).json()
        beta = api_client.post("/api/conversations", json={"title": f"Picker Beta {suffix}"}).json()

        page = authenticated_page
        page.locator("#message-input").fill("/list 10")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        picker = page.locator(".command-picker-overlay")
        picker.locator(".command-picker-input").fill(f"Beta {suffix}")
        page.wait_for_timeout(200)

        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_function(
            "(conversationId) => window.location.search.includes(`c=${conversationId}`)",
            arg=beta["id"],
            timeout=10000,
        )

        transcript = page.locator("#messages-container").text_content() or ""
        assert f"Picker Alpha {suffix}" not in transcript
        assert page.url.endswith(f"?c={beta['id']}")

    def test_list_picker_delete_action_requires_inline_confirmation(self, authenticated_page, api_client) -> None:
        resp = api_client.post("/api/conversations", json={"title": f"Delete Me {uuid.uuid4().hex[:8]}"})
        resp.raise_for_status()
        conv = resp.json()

        page = authenticated_page
        page.locator("#message-input").fill("/list 10")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("{conv["title"]}")').first
        assert target_card.count() == 1

        target_card.locator('.command-item-action:has-text("Delete")').click()
        confirm = target_card.locator(".command-item-confirm")
        assert confirm.is_visible()
        assert conv["title"] in (confirm.text_content() or "")

        confirm.locator('.command-item-confirm-cancel:has-text("Cancel")').click()
        page.wait_for_timeout(200)
        assert target_card.locator(".command-item-confirm").count() == 0

    def test_list_picker_confirm_delete_removes_conversation(self, authenticated_page, api_client) -> None:
        resp = api_client.post("/api/conversations", json={"title": f"Delete Confirm {uuid.uuid4().hex[:8]}"})
        resp.raise_for_status()
        conv = resp.json()

        page = authenticated_page
        page.locator("#message-input").fill("/list 20")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("{conv["title"]}")').first
        target_card.locator('.command-item-action:has-text("Delete")').click()
        confirm = target_card.locator(".command-item-confirm")
        confirm.locator('.command-item-confirm-go').click()

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(300)

        conversations = api_client.get("/api/conversations").json()
        assert all(existing["id"] != conv["id"] for existing in conversations)

    def test_list_picker_cancel_delete_keeps_conversation(self, authenticated_page, api_client) -> None:
        resp = api_client.post("/api/conversations", json={"title": f"Delete Cancel {uuid.uuid4().hex[:8]}"})
        resp.raise_for_status()
        conv = resp.json()

        page = authenticated_page
        page.locator("#message-input").fill("/list 20")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("{conv["title"]}")').first
        target_card.locator('.command-item-action:has-text("Delete")').click()
        target_card.locator(".command-item-confirm-cancel").click()

        page.wait_for_timeout(300)
        assert target_card.locator(".command-item-confirm").count() == 0

        conversations = api_client.get("/api/conversations").json()
        assert any(existing["id"] == conv["id"] for existing in conversations)

    def test_typed_delete_requires_confirmation_before_removing_conversation(self, authenticated_page, api_client) -> None:
        resp = api_client.post("/api/conversations", json={"title": f"Typed Delete {uuid.uuid4().hex[:8]}"})
        resp.raise_for_status()
        conv = resp.json()

        page = authenticated_page
        page.locator("#message-input").fill(f"/delete {conv['id']}")
        page.locator("#message-input").press("Enter")

        confirm_card = page.locator(f'.command-item:has-text("{conv["title"]}")').first
        page.wait_for_timeout(200)
        assert confirm_card.count() == 1
        assert "Confirm deleting" in (page.locator("#messages-container").text_content() or "")
        assert any(existing["id"] == conv["id"] for existing in api_client.get("/api/conversations").json())

        confirm_card.locator('.command-item-action:has-text("Delete")').click()
        confirm_card.locator(".command-item-confirm-go").click()
        page.wait_for_timeout(300)
        assert all(existing["id"] != conv["id"] for existing in api_client.get("/api/conversations").json())

    def test_skills_picker_inserts_skill_invocation_into_composer(self, authenticated_page) -> None:
        page = authenticated_page
        page.locator("#message-input").fill("/skills")
        page.locator("#message-input").press("Enter")

        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)
        assert page.locator(".command-picker-title").text_content() == "Skill Results"

        insert_btn = page.locator('.command-item-action:has-text("Insert")').first
        assert insert_btn.count() >= 1
        insert_btn.click()

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=5000)
        composer_value = page.locator("#message-input").input_value()
        assert composer_value.startswith("/")
        assert composer_value != "/skills"

    def test_plan_on_command_enables_plan_mode(self, authenticated_page) -> None:
        page = authenticated_page

        page.locator("#message-input").fill("/plan on")
        page.locator("#message-input").press("Enter")

        page.wait_for_function("() => typeof App !== 'undefined' && App.state.isPlanMode", timeout=10000)
        assert page.evaluate("() => App.state.isPlanMode") is True
        assert page.locator("#btn-plan-toggle").evaluate("el => el.classList.contains('active')") is True

    def test_plan_off_command_disables_plan_mode(self, authenticated_page) -> None:
        page = authenticated_page

        page.locator("#message-input").fill("/plan on")
        page.locator("#message-input").press("Enter")
        page.wait_for_function("() => typeof App !== 'undefined' && App.state.isPlanMode", timeout=10000)

        page.locator("#message-input").fill("/plan off")
        page.locator("#message-input").press("Enter")

        page.wait_for_function("() => typeof App !== 'undefined' && App.state.isPlanMode === false", timeout=10000)
        assert page.evaluate("() => App.state.isPlanMode") is False
        assert page.locator("#btn-plan-toggle").evaluate("el => !el.classList.contains('active')") is True

    def test_spaces_picker_switch_updates_active_conversation_space(self, authenticated_page, api_client) -> None:
        space = api_client.post(
            "/api/spaces",
            json={"name": f"picker-space-{uuid.uuid4().hex[:8]}"},
        ).json()
        conv = api_client.post("/api/conversations", json={"title": f"Space Target {uuid.uuid4().hex[:8]}"}).json()

        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        page.locator("#message-input").fill("/spaces")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("{space["name"]}")').first
        target_card.locator('.command-item-action:has-text("Switch")').click()

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(300)

        toast = page.locator(".toast").last
        assert space["name"] in (toast.text_content() or "")

        detail = api_client.get(f"/api/conversations/{conv['id']}").json()
        assert detail["space_id"] == space["id"]

    def test_spaces_picker_enter_switches_selected_space(self, authenticated_page, api_client) -> None:
        suffix = uuid.uuid4().hex[:8]
        api_client.post("/api/spaces", json={"name": f"alpha-space-{suffix}"}).json()
        target_space = api_client.post("/api/spaces", json={"name": f"beta-space-{suffix}"}).json()
        conv = api_client.post("/api/conversations", json={"title": f"Space Keyboard {suffix}"}).json()

        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        page.locator("#message-input").fill("/spaces")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        picker = page.locator(".command-picker-overlay")
        picker.locator(".command-picker-input").fill(f"beta-space-{suffix}")
        page.wait_for_timeout(200)

        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(300)

        toast = page.locator(".toast").last
        assert target_space["name"] in (toast.text_content() or "")

        detail = api_client.get(f"/api/conversations/{conv['id']}").json()
        assert detail["space_id"] == target_space["id"]

    def test_spaces_picker_delete_removes_space_after_confirmation(self, authenticated_page, api_client) -> None:
        suffix = uuid.uuid4().hex[:8]
        target_space = api_client.post("/api/spaces", json={"name": f"delete-space-{suffix}"}).json()
        conv = api_client.post("/api/conversations", json={"title": f"Space Delete {suffix}"}).json()

        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        page.locator("#message-input").fill("/spaces")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("{target_space["name"]}")').first
        target_card.locator('.command-item-action:has-text("Delete")').click()
        target_card.locator(".command-item-confirm-go").click()

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(300)

        toast = page.locator(".toast").last
        assert target_space["name"] in (toast.text_content() or "")

        spaces = api_client.get("/api/spaces").json()
        assert all(space["id"] != target_space["id"] for space in spaces)

    def test_space_create_command_creates_space_and_refreshes_sidebar(self, authenticated_page, api_client) -> None:
        suffix = uuid.uuid4().hex[:8]
        target_name = f"created-space-{suffix}"
        page = authenticated_page

        page.locator("#message-input").fill(f"/space create {target_name}")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(400)

        toast = page.locator(".toast").last
        assert target_name in (toast.text_content() or "")

        spaces = api_client.get("/api/spaces").json()
        assert any(space["name"] == target_name for space in spaces)

    def test_space_edit_name_command_updates_active_space(self, authenticated_page, api_client) -> None:
        suffix = uuid.uuid4().hex[:8]
        original_name = f"editable-space-{suffix}"
        updated_name = f"renamed-space-{suffix}"
        space = api_client.post("/api/spaces", json={"name": original_name}).json()
        conv = api_client.post("/api/conversations", json={"title": f"Space Edit {suffix}"}).json()

        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        page.locator("#message-input").fill(f"/space switch {original_name}")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(400)

        page.locator("#message-input").fill(f"/space edit name {updated_name}")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(400)

        toast = page.locator(".toast").last
        assert updated_name in (toast.text_content() or "")

        spaces = api_client.get("/api/spaces").json()
        assert any(item["id"] == space["id"] and item["name"] == updated_name for item in spaces)

    def test_space_refresh_command_updates_active_space_from_disk(self, authenticated_page, api_client, tmp_path) -> None:
        suffix = uuid.uuid4().hex[:8]
        source_file = tmp_path / f"refresh-space-{suffix}.yaml"
        source_file.write_text(
            "name: refresh-space\nversion: '1'\ninstructions: Original rules.\nconfig:\n  model: gpt-5.2\n"
        )
        space = api_client.post(
            "/api/spaces",
            json={"name": f"refresh-space-{suffix}", "source_file": str(source_file)},
        ).json()
        conv = api_client.post("/api/conversations", json={"title": f"Space Refresh {suffix}"}).json()

        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        page.locator("#message-input").fill(f"/space switch {space['name']}")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(400)

        source_file.write_text(
            "name: refresh-space\nversion: '1'\ninstructions: Refreshed rules.\nconfig:\n  model: gpt-5.4-mini\n"
        )
        page.locator("#message-input").fill("/spaces")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)
        target_card = page.locator(f'.command-item:has-text("{space["name"]}")').first
        target_card.locator('.command-item-action:has-text("Refresh")').click()
        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(500)

        toast = page.locator(".toast").last
        assert "Refreshed space" in (toast.text_content() or "")

        updated = api_client.get(f"/api/spaces/{space['id']}").json()
        assert updated["model"] == "gpt-5.4-mini"
        assert updated["instructions"] == "Refreshed rules."

    def test_space_export_action_renders_yaml_from_picker(self, authenticated_page, api_client) -> None:
        suffix = uuid.uuid4().hex[:8]
        space = api_client.post("/api/spaces", json={"name": f"export-space-{suffix}"}).json()
        conv = api_client.post("/api/conversations", json={"title": f"Space Export {suffix}"}).json()

        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        page.locator("#message-input").fill(f"/space switch {space['name']}")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(400)

        page.locator("#message-input").fill("/spaces")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)
        target_card = page.locator(f'.command-item:has-text("{space["name"]}")').first
        target_card.locator('.command-item-action:has-text("Export")').click()
        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)

        page.wait_for_function(
            "() => document.querySelector('#messages-container')?.textContent?.includes('Space YAML')",
            timeout=10000,
        )
        transcript = page.locator("#messages-container").text_content() or ""
        assert "Space YAML" in transcript
        assert space["name"] in transcript

    def test_partial_stream_error_shows_retry_and_retry_recovers(self, authenticated_page, api_client) -> None:
        conv = api_client.post("/api/conversations", json={"title": f"Retry Button {uuid.uuid4().hex[:8]}"}).json()
        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)

        _install_chat_stream_mock(
            page,
            [
                [
                    {"event": "token", "data": {"content": "Partial answer"}},
                    {"delayMs": 200, "event": "error", "data": {"message": "stream interrupted for test"}},
                ],
                [
                    {"event": "token", "data": {"content": "Recovered on retry."}},
                    {"event": "done", "data": {}},
                ],
            ],
        )
        try:
            page.locator("#message-input").fill("Trigger retry button")
            page.locator("#message-input").press("Enter")

            page.wait_for_selector(".error-message .btn-retry", timeout=10000)
            bubble = page.locator(".message.assistant").last
            assert "Partial answer" in (bubble.text_content() or "")
            assert "stream interrupted for test" in (bubble.text_content() or "")

            page.locator(".error-message .btn-retry").click()
            page.wait_for_function(
                "() => document.querySelector('#messages-container')?.textContent?.includes('Recovered on retry.')",
                timeout=10000,
            )
        finally:
            _restore_chat_stream_mock(page)

        transcript = page.locator("#messages-container").text_content() or ""
        assert "Recovered on retry." in transcript

    def test_watchdog_timeout_toast_surfaces_when_no_events_arrive(self, authenticated_page, api_client) -> None:
        conv = api_client.post("/api/conversations", json={"title": f"Watchdog {uuid.uuid4().hex[:8]}"}).json()
        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)

        page.evaluate(
            """() => {
                if (window.__origSetTimeoutForE2E) return;
                window.__origSetTimeoutForE2E = window.setTimeout.bind(window);
                window.setTimeout = (fn, ms, ...args) => {
                    const clamped = ms >= 45000 ? 250 : ms;
                    return window.__origSetTimeoutForE2E(fn, clamped, ...args);
                };
            }"""
        )

        try:
            _install_chat_stream_mock(page, [[{"delayMs": 1000, "close": True}]])
            try:
                page.locator("#message-input").fill("Trigger watchdog timeout")
                page.locator("#message-input").press("Enter")
                page.wait_for_function(
                    """
                    () => Array.from(document.querySelectorAll('.toast')).some(
                        el => (el.textContent || '').includes('No response from server after')
                    )
                    """,
                    timeout=10000,
                )
            finally:
                _restore_chat_stream_mock(page)
        finally:
            page.evaluate(
                """() => {
                    if (window.__origSetTimeoutForE2E) {
                        window.setTimeout = window.__origSetTimeoutForE2E;
                        delete window.__origSetTimeoutForE2E;
                    }
                }"""
            )

        assert page.locator(".toast").last.text_content().startswith("No response from server after")

    def test_tool_batch_renders_running_and_complete_states(self, authenticated_page, api_client) -> None:
        conv = api_client.post("/api/conversations", json={"title": f"Tool Batch {uuid.uuid4().hex[:8]}"}).json()
        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)

        _install_chat_stream_mock(
            page,
            [[
                {"event": "tool_batch_start", "data": {"call_count": 2}},
                {
                    "event": "tool_call_start",
                    "data": {"id": "tool-read-1", "tool_name": "read_file", "input": {"path": "src/app.py"}},
                },
                {
                    "event": "tool_call_end",
                    "data": {"id": "tool-read-1", "status": "success", "output": {"text": "ok"}},
                },
                {
                    "event": "tool_call_start",
                    "data": {"id": "tool-search-1", "tool_name": "grep", "input": {"pattern": "TODO"}},
                },
                {
                    "delayMs": 400,
                    "event": "tool_call_end",
                    "data": {"id": "tool-search-1", "status": "success", "output": {"matches": 3}},
                },
                {"event": "tool_batch_end", "data": {"call_count": 2, "elapsed_seconds": 1.4}},
                {"event": "token", "data": {"content": "Done."}},
                {"event": "done", "data": {}},
            ]],
        )
        try:
            page.locator("#message-input").fill("Trigger tool batch")
            page.locator("#message-input").press("Enter")

            page.wait_for_function(
                """
                () => {
                    const batch = document.querySelector('.tool-batch[data-status="running"]');
                    return !!batch && batch.textContent.includes("Searching 'TODO'");
                }
                """,
                timeout=10000,
            )
            page.wait_for_function(
                """
                () => {
                    const batch = document.querySelector('.tool-batch[data-status="complete"]');
                    return !!batch && batch.textContent.includes('Read src/app.py and Searched');
                }
                """,
                timeout=10000,
            )
        finally:
            _restore_chat_stream_mock(page)

    def test_subagent_card_renders_progress_and_completion(self, authenticated_page, api_client) -> None:
        conv = api_client.post("/api/conversations", json={"title": f"Subagent {uuid.uuid4().hex[:8]}"}).json()
        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)

        _install_chat_stream_mock(
            page,
            [[
                {
                    "event": "tool_call_start",
                    "data": {"id": "run-agent-1", "tool_name": "run_agent", "input": {"prompt": "Audit the config"}},
                },
                {
                    "event": "subagent_event",
                    "data": {
                        "kind": "subagent_start",
                        "agent_id": "agent-1",
                        "model": "gpt-5-mini",
                        "prompt": "Audit the config",
                    },
                },
                {
                    "event": "subagent_event",
                    "data": {"kind": "tool_call_start", "agent_id": "agent-1", "tool_name": "grep"},
                },
                {"delayMs": 300, "event": "subagent_event", "data": {
                    "kind": "subagent_end",
                    "agent_id": "agent-1",
                    "elapsed_seconds": 1.2,
                    "tool_calls": [{"tool_name": "grep"}],
                }},
                {
                    "event": "tool_call_end",
                    "data": {"id": "run-agent-1", "status": "success", "output": {"result": "ok"}},
                },
                {"event": "done", "data": {}},
            ]],
        )
        try:
            page.locator("#message-input").fill("Trigger subagent")
            page.locator("#message-input").press("Enter")

            page.wait_for_function(
                """
                () => {
                    const card = document.querySelector('.subagent-card');
                    return !!card && card.textContent.includes('Audit the config') && card.textContent.includes('grep');
                }
                """,
                timeout=10000,
            )
            page.wait_for_function(
                """
                () => {
                    const footer = document.querySelector('.subagent-card .subagent-footer');
                    return !!footer && footer.textContent.includes('Done in 1.2s');
                }
                """,
                timeout=10000,
            )
        finally:
            _restore_chat_stream_mock(page)

    def test_artifacts_picker_confirm_delete_removes_artifact(self, authenticated_page, api_client, app_server) -> None:
        suffix = uuid.uuid4().hex[:8]
        fqn = f"@e2e/skill/browser-{suffix}"
        db = _e2e_db(app_server)
        create_artifact(db, fqn, "skill", "e2e", f"browser-{suffix}", "content", source="local")

        page = authenticated_page
        page.locator("#message-input").fill("/artifacts")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("{fqn}")').first
        target_card.locator('.command-item-action:has-text("Delete")').click()
        target_card.locator(".command-item-confirm-go").click()

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(300)

        resp = api_client.get(f"/api/artifacts/{fqn}")
        assert resp.status_code == 404

    def test_artifacts_picker_cancel_delete_keeps_artifact(self, authenticated_page, api_client, app_server) -> None:
        suffix = uuid.uuid4().hex[:8]
        fqn = f"@e2e/skill/cancel-{suffix}"
        db = _e2e_db(app_server)
        create_artifact(db, fqn, "skill", "e2e", f"cancel-{suffix}", "content", source="local")

        page = authenticated_page
        page.locator("#message-input").fill("/artifacts")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("{fqn}")').first
        target_card.locator('.command-item-action:has-text("Delete")').click()
        target_card.locator(".command-item-confirm-cancel").click()

        page.wait_for_timeout(300)
        assert target_card.locator(".command-item-confirm").count() == 0

        resp = api_client.get(f"/api/artifacts/{fqn}")
        assert resp.status_code == 200

    def test_packs_picker_confirm_remove_removes_pack(
        self, authenticated_page, api_client, app_server, tmp_path
    ) -> None:
        suffix = uuid.uuid4().hex[:8]
        namespace = "e2e"
        name = f"browser-pack-{suffix}"
        db = _e2e_db(app_server)
        pack_dir = _create_e2e_pack_dir(tmp_path, name=name, namespace=namespace)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        ref = f"{namespace}/{name}"

        page = authenticated_page
        page.locator("#message-input").fill("/packs")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("{ref}")').first
        target_card.locator('.command-item-action:has-text("Remove")').click()
        target_card.locator(".command-item-confirm-go").click()

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(300)

        resp = api_client.get(f"/api/packs/{namespace}/{name}")
        assert resp.status_code == 404

    def test_packs_picker_cancel_remove_keeps_pack(self, authenticated_page, api_client, app_server, tmp_path) -> None:
        suffix = uuid.uuid4().hex[:8]
        namespace = "e2e"
        name = f"browser-pack-cancel-{suffix}"
        db = _e2e_db(app_server)
        pack_dir = _create_e2e_pack_dir(tmp_path, name=name, namespace=namespace)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        ref = f"{namespace}/{name}"

        page = authenticated_page
        page.locator("#message-input").fill("/packs")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("{ref}")').first
        target_card.locator('.command-item-action:has-text("Remove")').click()
        target_card.locator(".command-item-confirm-cancel").click()

        page.wait_for_timeout(300)
        assert target_card.locator(".command-item-confirm").count() == 0

        resp = api_client.get(f"/api/packs/{namespace}/{name}")
        assert resp.status_code == 200

    def test_packs_picker_attach_adds_global_attachment(self, authenticated_page, api_client, app_server, tmp_path) -> None:
        suffix = uuid.uuid4().hex[:8]
        namespace = "e2e"
        name = f"browser-pack-attach-{suffix}"
        db = _e2e_db(app_server)
        pack_dir = _create_e2e_pack_dir(tmp_path, name=name, namespace=namespace)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        ref = f"{namespace}/{name}"

        page = authenticated_page
        page.locator("#message-input").fill("/packs")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("@{ref}")').first
        target_card.locator('.command-item-action:has-text("Attach")').click()

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(300)

        attachments = api_client.get(f"/api/packs/{namespace}/{name}/attachments").json()
        assert any(att["scope"] == "global" and att["project_path"] is None for att in attachments)

    def test_packs_picker_detach_removes_global_attachment(self, authenticated_page, api_client, app_server, tmp_path) -> None:
        suffix = uuid.uuid4().hex[:8]
        namespace = "e2e"
        name = f"browser-pack-detach-{suffix}"
        db = _e2e_db(app_server)
        pack_dir = _create_e2e_pack_dir(tmp_path, name=name, namespace=namespace)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        pack = api_client.get(f"/api/packs/{namespace}/{name}").json()
        attach_pack(db, pack["id"])

        ref = f"{namespace}/{name}"

        page = authenticated_page
        page.locator("#message-input").fill("/packs")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator(f'.command-item:has-text("@{ref}")').first
        target_card.locator('.command-item-action:has-text("Detach")').click()

        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(300)

        attachments = api_client.get(f"/api/packs/{namespace}/{name}/attachments").json()
        assert attachments == []

    def test_pack_add_source_command_updates_server_sources(self, authenticated_page, api_client) -> None:
        suffix = uuid.uuid4().hex[:8]
        url = f"https://github.com/e2e/packs-{suffix}.git"

        page = authenticated_page
        page.locator("#message-input").fill(f"/pack add-source {url}")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(500)

        sources = api_client.get("/api/packs/sources").json()
        assert any(source["url"] == url for source in sources)

    def test_pack_install_command_installs_pack(self, authenticated_page, api_client, app_server, tmp_path) -> None:
        suffix = uuid.uuid4().hex[:8]
        namespace = "e2e"
        name = f"browser-pack-install-{suffix}"
        pack_dir = _create_e2e_pack_dir(tmp_path, name=name, namespace=namespace)

        page = authenticated_page
        page.locator("#message-input").fill(f"/pack install {pack_dir}")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(500)

        resp = api_client.get(f"/api/packs/{namespace}/{name}")
        assert resp.status_code == 200

    def test_pack_update_command_updates_pack_version(self, authenticated_page, api_client, app_server, tmp_path) -> None:
        suffix = uuid.uuid4().hex[:8]
        namespace = "e2e"
        name = f"browser-pack-update-{suffix}"
        db = _e2e_db(app_server)
        pack_dir = _create_e2e_pack_dir(tmp_path, name=name, namespace=namespace)
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)

        with open(pack_dir / "pack.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "name": name,
                    "namespace": namespace,
                    "version": "1.1.0",
                    "description": "Browser test pack",
                    "artifacts": [{"type": "skill", "name": "hello"}],
                },
                f,
            )

        page = authenticated_page
        page.locator("#message-input").fill(f"/pack update {pack_dir}")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(500)

        resp = api_client.get(f"/api/packs/{namespace}/{name}")
        assert resp.status_code == 200
        assert resp.json()["version"] == "1.1.0"

    def test_rewind_command_reloads_transcript_with_truncated_history(
        self, authenticated_page, api_client, app_server
    ) -> None:
        suffix = uuid.uuid4().hex[:8]
        db = _e2e_db(app_server)
        conv = api_client.post("/api/conversations", json={"title": f"Rewind Browser {suffix}"}).json()

        create_message(db, conv["id"], "user", f"first user {suffix}")
        create_message(db, conv["id"], "assistant", f"first assistant {suffix}")
        create_message(db, conv["id"], "user", f"second user {suffix}")
        create_message(db, conv["id"], "assistant", f"second assistant {suffix}")

        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        before = page.locator("#messages-container").text_content() or ""
        assert f"second assistant {suffix}" in before

        page.locator("#message-input").fill("/rewind 1")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(600)

        detail = api_client.get(f"/api/conversations/{conv['id']}").json()
        contents = [message["content"] for message in detail["messages"]]
        assert contents == [f"first user {suffix}", f"first assistant {suffix}"]

        after = page.locator("#messages-container").text_content() or ""
        assert f"first assistant {suffix}" in after
        assert f"second assistant {suffix}" not in after

    def test_rewind_invalid_position_keeps_transcript_unchanged(
        self, authenticated_page, api_client, app_server
    ) -> None:
        suffix = uuid.uuid4().hex[:8]
        db = _e2e_db(app_server)
        conv = api_client.post("/api/conversations", json={"title": f"Rewind Invalid {suffix}"}).json()

        create_message(db, conv["id"], "user", f"first user {suffix}")
        create_message(db, conv["id"], "assistant", f"first assistant {suffix}")

        page = authenticated_page
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        before = page.locator("#messages-container").text_content() or ""
        page.locator("#message-input").fill("/rewind 99")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(500)

        after = page.locator("#messages-container").text_content() or ""
        assert before in after
        assert "Position 99 not found." in after

        detail = api_client.get(f"/api/conversations/{conv['id']}").json()
        contents = [message["content"] for message in detail["messages"]]
        assert contents == [f"first user {suffix}", f"first assistant {suffix}"]

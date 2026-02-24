"""Tests for the interactive conversation picker helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anteroom.cli.repl import _picker_format_preview, _picker_relative_time, _picker_type_badge


class TestPickerRelativeTime:
    def test_days_ago(self) -> None:
        ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        assert _picker_relative_time(ts) == "3d ago"

    def test_hours_ago(self) -> None:
        ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        assert _picker_relative_time(ts) == "5h ago"

    def test_minutes_ago(self) -> None:
        ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        assert _picker_relative_time(ts) == "10m ago"

    def test_just_now(self) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        assert _picker_relative_time(ts) == "just now"

    def test_invalid_timestamp(self) -> None:
        assert _picker_relative_time("not-a-date") == ""

    def test_empty_string(self) -> None:
        assert _picker_relative_time("") == ""

    def test_z_suffix(self) -> None:
        ts = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert _picker_relative_time(ts) == "1d ago"


class TestPickerTypeBadge:
    def test_note(self) -> None:
        assert _picker_type_badge("note") == "[note]"

    def test_document(self) -> None:
        assert _picker_type_badge("document") == "[doc]"

    def test_chat(self) -> None:
        assert _picker_type_badge("chat") == ""

    def test_unknown(self) -> None:
        assert _picker_type_badge("other") == ""


class TestPickerFormatPreview:
    def test_empty_messages(self) -> None:
        result = _picker_format_preview([])
        assert len(result) == 1
        assert result[0][1] == " (no messages)"
        assert "empty" in result[0][0]

    def test_user_message(self) -> None:
        msgs = [{"role": "user", "content": "Hello world"}]
        result = _picker_format_preview(msgs)
        assert any("You:" in frag[1] for frag in result)
        assert any("Hello world" in frag[1] for frag in result)

    def test_assistant_message(self) -> None:
        msgs = [{"role": "assistant", "content": "Hi there"}]
        result = _picker_format_preview(msgs)
        assert any("AI:" in frag[1] for frag in result)
        assert any("Hi there" in frag[1] for frag in result)

    def test_user_truncation_at_200(self) -> None:
        long_msg = "x" * 300
        msgs = [{"role": "user", "content": long_msg}]
        result = _picker_format_preview(msgs)
        content_frag = [f for f in result if "content" in f[0]][0]
        assert "..." in content_frag[1]
        assert len(content_frag[1]) < 250

    def test_assistant_truncation_at_300(self) -> None:
        long_msg = "y" * 500
        msgs = [{"role": "assistant", "content": long_msg}]
        result = _picker_format_preview(msgs)
        content_frag = [f for f in result if "content" in f[0]][0]
        assert "..." in content_frag[1]
        assert len(content_frag[1]) < 350

    def test_skips_empty_content(self) -> None:
        msgs = [{"role": "user", "content": ""}, {"role": "assistant", "content": "answer"}]
        result = _picker_format_preview(msgs)
        assert not any("You:" in frag[1] for frag in result)
        assert any("AI:" in frag[1] for frag in result)

    def test_skips_non_string_content(self) -> None:
        msgs = [{"role": "user", "content": None}, {"role": "assistant", "content": "ok"}]
        result = _picker_format_preview(msgs)
        assert any("AI:" in frag[1] for frag in result)

    def test_skips_system_messages(self) -> None:
        msgs = [{"role": "system", "content": "You are helpful"}, {"role": "user", "content": "Hi"}]
        result = _picker_format_preview(msgs)
        assert not any("system" in frag[1].lower() for frag in result)
        assert any("You:" in frag[1] for frag in result)

    def test_limits_to_last_8_messages(self) -> None:
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        result = _picker_format_preview(msgs)
        user_frags = [f for f in result if "You:" in f[1]]
        assert len(user_frags) == 8

    def test_newlines_replaced(self) -> None:
        msgs = [{"role": "user", "content": "line1\nline2\nline3"}]
        result = _picker_format_preview(msgs)
        content_frag = [f for f in result if "content" in f[0]][0]
        assert "\n\n" in content_frag[1]  # trailing newlines from formatting
        assert "line1 line2 line3" in content_frag[1]

    def test_multi_exchange(self) -> None:
        msgs = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "follow-up"},
            {"role": "assistant", "content": "response"},
        ]
        result = _picker_format_preview(msgs)
        user_frags = [f for f in result if "You:" in f[1]]
        ai_frags = [f for f in result if "AI:" in f[1]]
        assert len(user_frags) == 2
        assert len(ai_frags) == 2

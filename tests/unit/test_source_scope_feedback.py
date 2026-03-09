"""Tests for source scope feedback (#853) and RAG status rendering (#854)."""

from __future__ import annotations

from unittest.mock import MagicMock

from anteroom.services.storage import get_source_tag_ids_bulk


class TestGetSourceTagIdsBulk:
    def test_empty_input_returns_empty(self) -> None:
        db = MagicMock()
        result = get_source_tag_ids_bulk(db, [])
        assert result == {}
        db.execute_fetchall.assert_not_called()

    def test_single_source_single_tag(self) -> None:
        db = MagicMock()
        db.execute_fetchall.return_value = [
            {"source_id": "s1", "tag_id": "t1"},
        ]
        result = get_source_tag_ids_bulk(db, ["s1"])
        assert result == {"s1": ["t1"]}

    def test_multiple_sources_multiple_tags(self) -> None:
        db = MagicMock()
        db.execute_fetchall.return_value = [
            {"source_id": "s1", "tag_id": "t1"},
            {"source_id": "s1", "tag_id": "t2"},
            {"source_id": "s2", "tag_id": "t1"},
        ]
        result = get_source_tag_ids_bulk(db, ["s1", "s2"])
        assert result == {"s1": ["t1", "t2"], "s2": ["t1"]}

    def test_source_with_no_tags_absent_from_result(self) -> None:
        db = MagicMock()
        db.execute_fetchall.return_value = [
            {"source_id": "s1", "tag_id": "t1"},
        ]
        result = get_source_tag_ids_bulk(db, ["s1", "s2"])
        assert "s2" not in result

    def test_parameterized_query(self) -> None:
        db = MagicMock()
        db.execute_fetchall.return_value = []
        get_source_tag_ids_bulk(db, ["a", "b", "c"])
        args = db.execute_fetchall.call_args
        sql = args[0][0]
        params = args[0][1]
        assert "?,?,?" in sql
        assert params == ("a", "b", "c")


class TestRenderRagStatus:
    """Test the CLI renderer's render_rag_status function."""

    def test_ok_with_chunks(self) -> None:
        from anteroom.cli.renderer import render_rag_status

        # Should not raise; output goes to console
        render_rag_status("ok", chunk_count=3)

    def test_no_results(self) -> None:
        from anteroom.cli.renderer import render_rag_status

        render_rag_status("no_results", reason="no matching content")

    def test_failed(self) -> None:
        from anteroom.cli.renderer import render_rag_status

        render_rag_status("failed")

    def test_no_vec_support(self) -> None:
        from anteroom.cli.renderer import render_rag_status

        render_rag_status("no_vec_support")

    def test_silent_states_no_output(self) -> None:
        from unittest.mock import patch

        from anteroom.cli.renderer import render_rag_status

        for status in ("disabled", "no_config", "skipped_plan_mode", "skipped"):
            with patch("anteroom.cli.renderer.console") as mock_console:
                render_rag_status(status)
                mock_console.print.assert_not_called()


class TestRenderRagSourcesBadgeLabels:
    """Verify render_rag_sources uses 'knowledge'/'conversation' badge labels."""

    def test_source_chunk_uses_knowledge_badge(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from anteroom.cli.renderer import render_rag_sources

        chunk = SimpleNamespace(source_label="My Doc", source_type="source_chunk")
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_rag_sources([chunk])
            output = mock_console.print.call_args[0][0]
            assert "knowledge" in output
            assert "source" not in output.replace("knowledge", "")  # no bare "source" badge

    def test_message_uses_conversation_badge(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from anteroom.cli.renderer import render_rag_sources

        chunk = SimpleNamespace(source_label="Chat History", source_type="message")
        with patch("anteroom.cli.renderer.console") as mock_console:
            render_rag_sources([chunk])
            output = mock_console.print.call_args[0][0]
            assert "conversation" in output

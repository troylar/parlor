"""Tests for RAG source provenance metadata (#814, #822)."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO

from rich.console import Console


@dataclass
class _FakeChunk:
    """Minimal stand-in for RetrievedChunk."""

    content: str
    source_type: str
    source_label: str
    distance: float
    conversation_id: str | None = None
    message_id: str | None = None
    source_id: str | None = None
    chunk_id: str | None = None
    conversation_type: str | None = None


# ---------------------------------------------------------------------------
# prompt_meta enrichment logic
# ---------------------------------------------------------------------------


class TestRagSourcesMetadata:
    """Verify the rag_sources list structure matches what chat.py builds."""

    def test_rag_sources_structure_from_chunks(self) -> None:
        chunks = [
            _FakeChunk(
                content="chunk1",
                source_type="source_chunk",
                source_label="Q3 Report",
                distance=0.3,
                source_id="src-1",
            ),
            _FakeChunk(
                content="chunk2",
                source_type="message",
                source_label="previous chat",
                distance=0.5,
                conversation_id="conv-2",
                message_id="msg-1",
            ),
        ]

        # This mirrors the exact logic added to routers/chat.py
        rag_sources = [{"label": c.source_label, "type": c.source_type, "source_id": c.source_id} for c in chunks]

        assert len(rag_sources) == 2
        assert rag_sources[0] == {"label": "Q3 Report", "type": "source_chunk", "source_id": "src-1"}
        assert rag_sources[1] == {"label": "previous chat", "type": "message", "source_id": None}

    def test_rag_sources_empty_for_no_chunks(self) -> None:
        chunks: list[_FakeChunk] = []
        rag_sources = [{"label": c.source_label, "type": c.source_type, "source_id": c.source_id} for c in chunks]
        assert rag_sources == []

    def test_rag_sources_preserves_all_chunk_labels(self) -> None:
        chunks = [
            _FakeChunk(
                content=f"c{i}",
                source_type="source_chunk",
                source_label=f"doc-{i}",
                distance=0.1 * i,
                source_id=f"s-{i}",
            )
            for i in range(5)
        ]
        rag_sources = [{"label": c.source_label, "type": c.source_type, "source_id": c.source_id} for c in chunks]
        assert len(rag_sources) == 5
        assert [s["label"] for s in rag_sources] == ["doc-0", "doc-1", "doc-2", "doc-3", "doc-4"]


# ---------------------------------------------------------------------------
# CLI renderer (cli/renderer.py)
# ---------------------------------------------------------------------------


class TestRenderRagSources:
    """Verify render_rag_sources output."""

    def _capture(self, chunks: list[_FakeChunk]) -> str:
        from anteroom.cli import renderer

        buf = StringIO()
        test_console = Console(file=buf, width=120, force_terminal=True, no_color=True)
        original = renderer.console
        renderer.console = test_console
        try:
            renderer.render_rag_sources(chunks)
        finally:
            renderer.console = original
        return buf.getvalue()

    def test_renders_source_list(self) -> None:
        chunks = [
            _FakeChunk(
                content="c1",
                source_type="source_chunk",
                source_label="Q3 Report",
                distance=0.3,
                source_id="s1",
            ),
            _FakeChunk(
                content="c2",
                source_type="message",
                source_label="earlier chat",
                distance=0.5,
            ),
        ]
        output = self._capture(chunks)
        assert "Q3 Report" in output
        assert "knowledge" in output
        assert "earlier chat" in output
        assert "conversation" in output

    def test_noop_on_empty_list(self) -> None:
        assert self._capture([]) == ""

    def test_deduplicates_sources(self) -> None:
        chunks = [
            _FakeChunk(content="c1", source_type="source_chunk", source_label="Report", distance=0.2, source_id="s1"),
            _FakeChunk(content="c2", source_type="source_chunk", source_label="Report", distance=0.4, source_id="s1"),
        ]
        output = self._capture(chunks)
        assert output.count("Report") == 1

    def test_message_type_badge(self) -> None:
        chunks = [
            _FakeChunk(content="c1", source_type="message", source_label="old convo", distance=0.3),
        ]
        output = self._capture(chunks)
        assert "conversation" in output
        assert "old convo" in output

    def test_source_chunk_type_badge(self) -> None:
        chunks = [
            _FakeChunk(
                content="c1",
                source_type="source_chunk",
                source_label="manual.pdf",
                distance=0.2,
                source_id="s1",
            ),
        ]
        output = self._capture(chunks)
        assert "knowledge" in output
        assert "manual.pdf" in output

    def test_handles_missing_attributes_gracefully(self) -> None:
        """Chunks with missing attributes should fall back to '?'."""

        class _BareChunk:
            pass

        output = self._capture([_BareChunk()])  # type: ignore[arg-type]
        assert "?" in output

    def test_special_characters_in_label(self) -> None:
        chunks = [
            _FakeChunk(
                content="c1",
                source_type="source_chunk",
                source_label='Report "Q3" <2024>',
                distance=0.2,
                source_id="s1",
            ),
        ]
        output = self._capture(chunks)
        assert "Report" in output


class TestRenderRagSourcesDicts:
    """Verify render_rag_sources handles dicts from persisted metadata (#822)."""

    def _capture(self, chunks: list[dict]) -> str:  # type: ignore[type-arg]
        from anteroom.cli import renderer

        buf = StringIO()
        test_console = Console(file=buf, width=120, force_terminal=True, no_color=True)
        original = renderer.console
        renderer.console = test_console
        try:
            renderer.render_rag_sources(chunks)
        finally:
            renderer.console = original
        return buf.getvalue()

    def test_renders_dict_sources(self) -> None:
        sources = [
            {"label": "Q3 Report", "type": "source_chunk", "source_id": "s1"},
            {"label": "earlier chat", "type": "message", "source_id": "m1"},
        ]
        output = self._capture(sources)
        assert "Q3 Report" in output
        assert "knowledge" in output
        assert "earlier chat" in output
        assert "conversation" in output

    def test_deduplicates_dict_sources(self) -> None:
        sources = [
            {"label": "Report", "type": "source_chunk", "source_id": "s1"},
            {"label": "Report", "type": "source_chunk", "source_id": "s1"},
        ]
        output = self._capture(sources)
        assert output.count("Report") == 1

    def test_empty_dict_list(self) -> None:
        assert self._capture([]) == ""

    def test_mixed_objects_and_dicts(self) -> None:
        """render_rag_sources handles a mix of objects and dicts in the same list."""
        chunks: list = [
            _FakeChunk(content="c1", source_type="source_chunk", source_label="obj.pdf", distance=0.2, source_id="s1"),
            {"label": "dict.pdf", "type": "source_chunk", "source_id": "s2"},
        ]
        output = self._capture(chunks)
        assert "obj.pdf" in output
        assert "dict.pdf" in output

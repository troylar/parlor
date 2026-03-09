"""Tests for source storage operations (CRUD, chunking, tags, groups, project linking, dual citizenship)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anteroom.db import _SCHEMA, _VEC_METADATA_SCHEMA, ThreadSafeConnection
from anteroom.services.document_extractor import ExtractionResult
from anteroom.services.storage import (
    _validate_upload,
    add_source_to_group,
    add_tag_to_source,
    chunk_text,
    create_conversation,
    create_message,
    create_source,
    create_source_chunks,
    create_source_from_attachment,
    create_source_group,
    create_tag,
    delete_source,
    delete_source_group,
    get_source,
    get_source_embedding_status,
    get_source_group,
    get_source_tags,
    get_unembedded_source_chunks,
    list_source_chunks,
    list_source_groups,
    list_sources,
    remove_source_from_group,
    remove_tag_from_source,
    reprocess_source,
    save_attachment,
    save_source_file,
    update_source,
    update_source_group,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_VEC_METADATA_SCHEMA)
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return ThreadSafeConnection(conn)


class TestChunkText:
    def test_empty_text(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_short_text_single_chunk(self) -> None:
        text = "This is a short sentence."
        result = chunk_text(text, max_size=100)
        assert result == [text]

    def test_long_text_splits_at_sentence_boundaries(self) -> None:
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        result = chunk_text(text, max_size=40, overlap=0)
        assert len(result) >= 2
        assert all(len(chunk) <= 60 for chunk in result)  # some slack for sentence boundaries

    def test_overlap_produces_overlapping_chunks(self) -> None:
        sentences = [f"Sentence number {i}." for i in range(20)]
        text = " ".join(sentences)
        result = chunk_text(text, max_size=100, overlap=30)
        assert len(result) >= 2


class TestSourceCRUD:
    def test_create_text_source(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Test Note", content="Some content here.")
        assert source["type"] == "text"
        assert source["title"] == "Test Note"
        assert source["content"] == "Some content here."
        assert source["content_hash"] is not None

    def test_create_url_source(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="url", title="Link", url="https://example.com")
        assert source["type"] == "url"
        assert source["url"] == "https://example.com"

    def test_create_source_invalid_type_raises(self, db: ThreadSafeConnection) -> None:
        with pytest.raises(ValueError, match="Invalid source type"):
            create_source(db, source_type="invalid", title="Bad")

    def test_get_source(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Test", content="Content here for testing.")
        fetched = get_source(db, source["id"])
        assert fetched is not None
        assert fetched["id"] == source["id"]
        assert "tags" in fetched
        assert "chunks" in fetched

    def test_get_source_missing(self, db: ThreadSafeConnection) -> None:
        assert get_source(db, "nonexistent") is None

    def test_list_sources_empty(self, db: ThreadSafeConnection) -> None:
        assert list_sources(db) == []

    def test_list_sources_with_search(self, db: ThreadSafeConnection) -> None:
        create_source(db, source_type="text", title="Alpha", content="First source content.")
        create_source(db, source_type="text", title="Beta", content="Second source content.")
        result = list_sources(db, search="Alpha")
        assert len(result) == 1
        assert result[0]["title"] == "Alpha"

    def test_list_sources_by_type(self, db: ThreadSafeConnection) -> None:
        create_source(db, source_type="text", title="Note", content="Some note.")
        create_source(db, source_type="url", title="Link", url="https://example.com")
        result = list_sources(db, source_type="text")
        assert len(result) == 1
        assert result[0]["type"] == "text"

    def test_update_source_title(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Old", content="Content here for update.")
        updated = update_source(db, source["id"], title="New")
        assert updated is not None
        assert updated["title"] == "New"

    def test_update_source_content_rechunks(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Note", content="Original content here.")
        update_source(db, source["id"], content="Updated content with new text here.")
        new_chunks = list_source_chunks(db, source["id"])
        assert new_chunks[0]["content"] == "Updated content with new text here."

    def test_update_source_missing(self, db: ThreadSafeConnection) -> None:
        assert update_source(db, "nonexistent", title="New") is None

    def test_delete_source(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Delete me", content="Content.")
        assert delete_source(db, source["id"]) is True
        assert get_source(db, source["id"]) is None

    def test_delete_source_missing(self, db: ThreadSafeConnection) -> None:
        assert delete_source(db, "nonexistent") is False


class TestSourceChunks:
    def test_create_and_list_chunks(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Test", content="Short.")
        chunks = create_source_chunks(db, source["id"], ["chunk 1", "chunk 2"])
        assert len(chunks) == 2
        assert chunks[0]["chunk_index"] == 0
        assert chunks[1]["chunk_index"] == 1

        listed = list_source_chunks(db, source["id"])
        assert len(listed) >= 2  # may include auto-chunks from create_source

    def test_auto_chunking_on_create(self, db: ThreadSafeConnection) -> None:
        long_text = "First sentence. " * 100
        source, _ = create_source(db, source_type="text", title="Long", content=long_text)
        chunks = list_source_chunks(db, source["id"])
        assert len(chunks) > 1

    def test_get_unembedded_source_chunks(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(
            db, source_type="text", title="Test", content="Long enough content for embedding test."
        )
        unembedded = get_unembedded_source_chunks(db)
        assert len(unembedded) > 0
        assert unembedded[0]["source_id"] == source["id"]


class TestSourceTags:
    def test_add_and_get_tags(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Test", content="Content.")
        tag = create_tag(db, name="important")
        assert add_tag_to_source(db, source["id"], tag["id"]) is True
        tags = get_source_tags(db, source["id"])
        assert len(tags) == 1
        assert tags[0]["name"] == "important"

    def test_remove_tag(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Test", content="Content.")
        tag = create_tag(db, name="temp")
        add_tag_to_source(db, source["id"], tag["id"])
        remove_tag_from_source(db, source["id"], tag["id"])
        tags = get_source_tags(db, source["id"])
        assert len(tags) == 0

    def test_list_sources_by_tag(self, db: ThreadSafeConnection) -> None:
        s1, _ = create_source(db, source_type="text", title="Tagged", content="Content.")
        create_source(db, source_type="text", title="Not tagged", content="Content.")
        tag = create_tag(db, name="filter-tag")
        add_tag_to_source(db, s1["id"], tag["id"])
        result = list_sources(db, tag_id=tag["id"])
        assert len(result) == 1
        assert result[0]["id"] == s1["id"]


class TestSourceGroups:
    def test_create_and_list_groups(self, db: ThreadSafeConnection) -> None:
        group = create_source_group(db, name="Research Papers")
        assert group["name"] == "Research Papers"
        groups = list_source_groups(db)
        assert len(groups) == 1

    def test_update_group(self, db: ThreadSafeConnection) -> None:
        group = create_source_group(db, name="Old Name")
        updated = update_source_group(db, group["id"], name="New Name")
        assert updated is not None
        assert updated["name"] == "New Name"

    def test_update_group_missing(self, db: ThreadSafeConnection) -> None:
        assert update_source_group(db, "nonexistent", name="Name") is None

    def test_delete_group(self, db: ThreadSafeConnection) -> None:
        group = create_source_group(db, name="Delete Me")
        assert delete_source_group(db, group["id"]) is True
        assert get_source_group(db, group["id"]) is None

    def test_add_remove_source_from_group(self, db: ThreadSafeConnection) -> None:
        group = create_source_group(db, name="Group")
        source, _ = create_source(db, source_type="text", title="S1", content="Content.")
        assert add_source_to_group(db, group["id"], source["id"]) is True

        result = list_sources(db, group_id=group["id"])
        assert len(result) == 1

        remove_source_from_group(db, group["id"], source["id"])
        result = list_sources(db, group_id=group["id"])
        assert len(result) == 0


class TestDualCitizenship:
    def test_create_source_from_text_attachment(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        conv = create_conversation(db, title="Test")
        msg = create_message(db, conv["id"], "user", "check this file")

        # Save attachment
        data_dir = tmp_path
        att = save_attachment(
            db,
            msg["id"],
            conv["id"],
            "notes.txt",
            "text/plain",
            b"Hello from the file!",
            data_dir,
        )

        source = create_source_from_attachment(db, att["id"], data_dir)
        assert source is not None
        assert source["type"] == "file"
        assert source["filename"] == "notes.txt"
        assert source["content"] == "Hello from the file!"

    def test_create_source_from_missing_attachment(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        result = create_source_from_attachment(db, "nonexistent", tmp_path)
        assert result is None


class TestSaveSourceFile:
    def test_save_text_file(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        source, _ = save_source_file(
            db,
            title="readme",
            filename="README.md",
            mime_type="text/markdown",
            data=b"# Hello\n\nWorld",
            data_dir=tmp_path,
        )
        assert source["type"] == "file"
        assert source["content"] == "# Hello\n\nWorld"
        assert source["size_bytes"] == len(b"# Hello\n\nWorld")

    def test_save_file_validates_mime(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unsupported file type"):
            save_source_file(
                db,
                title="bad",
                filename="test.exe",
                mime_type="application/x-executable",
                data=b"binary data",
                data_dir=tmp_path,
            )

    def test_save_file_validates_size(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="maximum size"):
            save_source_file(
                db,
                title="big",
                filename="big.txt",
                mime_type="text/plain",
                data=b"x" * (11 * 1024 * 1024),
                data_dir=tmp_path,
            )

    def test_save_docx_file(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        # .docx files are ZIP containers — filetype detects application/zip
        # but the declared MIME is application/vnd.openxmlformats-...
        # Use minimal ZIP header to simulate a .docx
        zip_header = b"PK\x03\x04" + b"\x00" * 26
        source, _ = save_source_file(
            db,
            title="Document",
            filename="report.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=zip_header,
            data_dir=tmp_path,
        )
        assert source["type"] == "file"
        assert source["filename"] == "report.docx"

    def test_save_json_file(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        source, _ = save_source_file(
            db,
            title="Config",
            filename="config.json",
            mime_type="application/json",
            data=b'{"key": "value"}',
            data_dir=tmp_path,
        )
        assert source["type"] == "file"
        assert source["content"] == '{"key": "value"}'

    def test_save_yaml_file(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        source, _ = save_source_file(
            db,
            title="Config",
            filename="config.yaml",
            mime_type="application/x-yaml",
            data=b"key: value\n",
            data_dir=tmp_path,
        )
        assert source["type"] == "file"
        assert source["content"] == "key: value\n"


class TestValidateUpload:
    def test_text_plain_passes(self) -> None:
        _validate_upload("text/plain", b"hello world", "test.txt")

    def test_text_markdown_passes(self) -> None:
        _validate_upload("text/markdown", b"# Hello", "readme.md")

    def test_application_json_passes(self) -> None:
        _validate_upload("application/json", b'{"key": "value"}', "data.json")

    def test_application_yaml_passes(self) -> None:
        _validate_upload("application/x-yaml", b"key: value", "config.yaml")

    def test_application_toml_passes(self) -> None:
        _validate_upload("application/toml", b"[section]\nkey = 1", "config.toml")

    def test_application_sql_passes(self) -> None:
        _validate_upload("application/sql", b"SELECT 1", "query.sql")

    def test_unsupported_mime_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported file type"):
            _validate_upload("application/x-executable", b"binary", "bad.exe")

    def test_size_limit(self) -> None:
        with pytest.raises(ValueError, match="maximum size"):
            _validate_upload("text/plain", b"x" * (11 * 1024 * 1024), "big.txt")

    def test_png_magic_bytes_match(self) -> None:
        # PNG magic bytes
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        _validate_upload("image/png", png_data, "image.png")

    def test_mime_mismatch_rejected(self) -> None:
        # PNG magic bytes but claimed as JPEG
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        with pytest.raises(ValueError, match="does not match"):
            _validate_upload("image/jpeg", png_data, "fake.jpg")

    def test_docx_zip_container_allowed(self) -> None:
        # .docx is a ZIP container — filetype detects application/zip
        zip_header = b"PK\x03\x04" + b"\x00" * 26
        _validate_upload(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            zip_header,
            "report.docx",
        )

    def test_xlsx_zip_container_allowed(self) -> None:
        zip_header = b"PK\x03\x04" + b"\x00" * 26
        _validate_upload(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            zip_header,
            "data.xlsx",
        )

    def test_pptx_zip_container_allowed(self) -> None:
        zip_header = b"PK\x03\x04" + b"\x00" * 26
        _validate_upload(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            zip_header,
            "slides.pptx",
        )

    def test_legacy_xls_ole_container_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Legacy .xls files use OLE/CFB containers — filetype detects the container format
        from unittest.mock import MagicMock

        import anteroom.services.storage as storage_mod

        mock_guess = MagicMock()
        mock_guess.mime = "application/x-ole-storage"
        monkeypatch.setattr(storage_mod.filetype, "guess", lambda _: mock_guess)
        _validate_upload("application/vnd.ms-excel", b"\xd0\xcf\x11\xe0" + b"\x00" * 100, "data.xls")

    def test_octet_stream_utf8_text_passes(self) -> None:
        # Browser sent no MIME type — but content is valid UTF-8 and extension is text-like
        _validate_upload("application/octet-stream", b"Hello, this is a text file.", "readme.md")

    def test_octet_stream_binary_rejected(self) -> None:
        # Binary content with application/octet-stream should be rejected
        binary_data = bytes(range(256)) * 10
        with pytest.raises(ValueError, match="Cannot verify file content type"):
            _validate_upload("application/octet-stream", binary_data, "unknown.md")

    def test_octet_stream_bad_extension_rejected(self) -> None:
        # Valid UTF-8 content but non-text-like extension
        with pytest.raises(ValueError, match="Unsupported file type"):
            _validate_upload("application/octet-stream", b"hello", "file.exe")

    def test_octet_stream_no_extension_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported file type"):
            _validate_upload("application/octet-stream", b"hello", "noext")

    def test_octet_stream_json_extension_passes(self) -> None:
        _validate_upload("application/octet-stream", b'{"key": "value"}', "data.json")

    def test_octet_stream_py_extension_passes(self) -> None:
        _validate_upload("application/octet-stream", b"print('hello')", "script.py")

    def test_svg_rejected_xss_prevention(self) -> None:
        # SVG intentionally excluded — can contain embedded JavaScript (stored XSS)
        with pytest.raises(ValueError, match="Unsupported file type"):
            _validate_upload("image/svg+xml", b"<svg></svg>", "icon.svg")

    def test_html_passes(self) -> None:
        _validate_upload("text/html", b"<html><body>Hello</body></html>", "page.html")

    def test_rtf_passes(self) -> None:
        _validate_upload("application/rtf", b"{\\rtf1 hello}", "doc.rtf")

    def test_pdf_magic_bytes_match(self) -> None:
        pdf_data = b"%PDF-1.4 " + b"\x00" * 100
        _validate_upload("application/pdf", pdf_data, "doc.pdf")

    def test_unverifiable_binary_mime_rejected(self) -> None:
        # Binary MIME type where filetype.guess() returns None — should be rejected
        # (e.g., claiming image/png but providing content with no magic bytes)
        with pytest.raises(ValueError, match="Cannot verify file content type"):
            _validate_upload("image/png", b"\x00" * 200, "fake.png")


class TestSaveSourceFileExtraction:
    """Verify that save_source_file calls document_extractor for binary MIME types (#179)."""

    def test_pdf_extraction_populates_content(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        from unittest.mock import patch

        pdf_data = b"%PDF-1.4 " + b"\x00" * 100

        with patch("anteroom.services.storage._validate_upload"):
            with patch(
                "anteroom.services.document_extractor.extract_text",
                return_value=ExtractionResult(text="Extracted PDF text"),
            ):
                source, warnings = save_source_file(db, "test.pdf", "test.pdf", "application/pdf", pdf_data, tmp_path)

        assert source["content"] == "Extracted PDF text"
        assert warnings == []
        chunks = list_source_chunks(db, source["id"])
        assert len(chunks) >= 1
        assert chunks[0]["content"] == "Extracted PDF text"

    def test_docx_extraction_populates_content(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        from unittest.mock import patch

        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        docx_data = b"PK\x03\x04" + b"\x00" * 100

        with patch("anteroom.services.storage._validate_upload"):
            with patch(
                "anteroom.services.document_extractor.extract_text",
                return_value=ExtractionResult(text="DOCX content here"),
            ):
                source, _ = save_source_file(db, "doc.docx", "doc.docx", docx_mime, docx_data, tmp_path)

        assert source["content"] == "DOCX content here"
        chunks = list_source_chunks(db, source["id"])
        assert len(chunks) >= 1

    def test_extraction_returns_none_no_chunks(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        from unittest.mock import patch

        pdf_data = b"%PDF-1.4 " + b"\x00" * 100

        with patch("anteroom.services.storage._validate_upload"):
            with patch(
                "anteroom.services.document_extractor.extract_text",
                return_value=ExtractionResult(text=None),
            ):
                source, warnings = save_source_file(db, "empty.pdf", "empty.pdf", "application/pdf", pdf_data, tmp_path)

        assert source["content"] is None
        chunks = list_source_chunks(db, source["id"])
        assert len(chunks) == 0
        assert any("no text extracted" in w.lower() for w in warnings)

    def test_text_files_still_decoded_directly(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        """Text files should still use UTF-8 decode, not the document extractor."""
        from unittest.mock import patch

        text_data = b"Hello, plain text."

        with patch("anteroom.services.storage._validate_upload"):
            with patch("anteroom.services.document_extractor.extract_text") as mock_extract:
                source, _ = save_source_file(db, "readme.txt", "readme.txt", "text/plain", text_data, tmp_path)

        # extract_text should NOT have been called because UTF-8 decode succeeded
        mock_extract.assert_not_called()
        assert source["content"] == "Hello, plain text."


class TestSaveSourceFileDedup:
    def test_duplicate_content_returns_existing(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        data = b"# Hello\n\nWorld content here"
        source1, _ = save_source_file(
            db, title="first", filename="a.md", mime_type="text/markdown", data=data, data_dir=tmp_path
        )
        source2, _ = save_source_file(
            db, title="second", filename="b.md", mime_type="text/markdown", data=data, data_dir=tmp_path
        )
        assert source1["id"] == source2["id"]
        assert source1["content_hash"] == source2["content_hash"]

    def test_different_content_creates_new(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        source1, _ = save_source_file(
            db,
            title="first",
            filename="a.md",
            mime_type="text/markdown",
            data=b"content A long enough",
            data_dir=tmp_path,
        )
        source2, _ = save_source_file(
            db,
            title="second",
            filename="b.md",
            mime_type="text/markdown",
            data=b"content B long enough",
            data_dir=tmp_path,
        )
        assert source1["id"] != source2["id"]


class TestSaveSourceFileAtomicCleanup:
    def test_file_cleaned_up_on_db_failure(self, tmp_path: Path) -> None:
        """If DB insert fails, the written file should be cleaned up."""
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = _sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        # Intentionally do NOT create the sources table
        broken_db = ThreadSafeConnection(conn)

        data = b"test content long enough"
        with pytest.raises(Exception):
            save_source_file(
                broken_db,
                title="test",
                filename="test.txt",
                mime_type="text/plain",
                data=data,
                data_dir=tmp_path,
            )

        # No source directories should remain
        sources_dir = tmp_path / "sources"
        if sources_dir.exists():
            remaining = list(sources_dir.iterdir())
            assert remaining == []


class TestReprocessSource:
    def test_not_found(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        source, warnings = reprocess_source(db, "nonexistent-id", tmp_path)
        assert source == {}
        assert any("not found" in w.lower() for w in warnings)

    def test_no_storage_path(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        source, _ = create_source(db, source_type="text", title="Note", content="hello")
        result, warnings = reprocess_source(db, source["id"], tmp_path)
        assert result["id"] == source["id"]
        assert any("no stored file" in w.lower() for w in warnings)

    def test_file_missing_on_disk(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        source, _ = save_source_file(
            db,
            title="test.txt",
            filename="test.txt",
            mime_type="text/plain",
            data=b"hello world",
            data_dir=tmp_path,
        )
        # Delete the file on disk
        storage_path = source["storage_path"]
        (tmp_path / storage_path).unlink()
        result, warnings = reprocess_source(db, source["id"], tmp_path)
        assert any("not found" in w.lower() for w in warnings)

    def test_successful_reprocess(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        from unittest.mock import patch

        source, _ = save_source_file(
            db,
            title="test.txt",
            filename="test.txt",
            mime_type="text/plain",
            data=b"hello world",
            data_dir=tmp_path,
        )
        # Clear content to simulate failed initial extraction
        db.execute("UPDATE sources SET content = NULL WHERE id = ?", (source["id"],))
        db.commit()

        with patch(
            "anteroom.services.storage._try_extract",
            return_value=ExtractionResult(text="reprocessed text"),
        ):
            result, warnings = reprocess_source(db, source["id"], tmp_path)

        assert result["content"] == "reprocessed text"
        assert result["chunk_count"] > 0
        assert warnings == []

    def test_reprocess_rebuilds_chunks(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        from unittest.mock import patch

        source, _ = save_source_file(
            db,
            title="test.txt",
            filename="test.txt",
            mime_type="text/plain",
            data=b"hello world",
            data_dir=tmp_path,
        )
        old_chunks = list_source_chunks(db, source["id"])

        with patch(
            "anteroom.services.storage._try_extract",
            return_value=ExtractionResult(text="completely different content for reprocessing"),
        ):
            result, _ = reprocess_source(db, source["id"], tmp_path)

        new_chunks = list_source_chunks(db, source["id"])
        assert len(new_chunks) > 0
        # Chunk IDs should differ after rebuild
        old_ids = {c["id"] for c in old_chunks}
        new_ids = {c["id"] for c in new_chunks}
        assert old_ids != new_ids

    def test_reprocess_extraction_fails(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        from unittest.mock import patch

        source, _ = save_source_file(
            db,
            title="test.txt",
            filename="test.txt",
            mime_type="text/plain",
            data=b"hello world",
            data_dir=tmp_path,
        )

        with patch(
            "anteroom.services.storage._try_extract",
            return_value=ExtractionResult(text=None, warnings=["extraction failed"]),
        ):
            result, warnings = reprocess_source(db, source["id"], tmp_path)

        assert "extraction failed" in warnings
        assert result["chunk_count"] == 0

    def test_reprocess_no_text_no_warnings(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        from unittest.mock import patch

        source, _ = save_source_file(
            db,
            title="test.txt",
            filename="test.txt",
            mime_type="text/plain",
            data=b"hello world",
            data_dir=tmp_path,
        )

        with patch(
            "anteroom.services.storage._try_extract",
            return_value=ExtractionResult(text=None),
        ):
            result, warnings = reprocess_source(db, source["id"], tmp_path)

        assert any("re-extraction produced no text" in w.lower() for w in warnings)

    def test_reprocess_file_read_error(self, db: ThreadSafeConnection, tmp_path: Path) -> None:
        """reprocess_source returns a warning instead of raising when file can't be read."""
        from unittest.mock import patch

        source, _ = save_source_file(
            db,
            title="test.txt",
            filename="test.txt",
            mime_type="text/plain",
            data=b"hello world",
            data_dir=tmp_path,
        )

        # Mock read_bytes to raise OSError (e.g. permission denied)
        with patch("pathlib.Path.read_bytes", side_effect=OSError("Permission denied")):
            result, warnings = reprocess_source(db, source["id"], tmp_path)

        assert any("cannot read" in w.lower() for w in warnings)
        assert result.get("id") == source["id"]


class TestGetSourceEmbeddingStatus:
    def test_no_chunks(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="url", title="Empty", url="https://example.com")
        assert get_source_embedding_status(db, source["id"]) == "no_chunks"

    def test_pending(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Note", content="hello world")
        # create_source auto-chunks, so chunks exist but no embeddings
        assert get_source_embedding_status(db, source["id"]) == "pending"

    def test_embedded(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Note", content="hello world")
        chunks = list_source_chunks(db, source["id"])
        assert len(chunks) > 0
        for c in chunks:
            db.execute(
                "INSERT INTO source_chunk_embeddings (chunk_id, source_id, content_hash, status, created_at) "
                "VALUES (?, ?, 'hash', 'embedded', '2024-01-01T00:00:00')",
                (c["id"], source["id"]),
            )
        db.commit()
        assert get_source_embedding_status(db, source["id"]) == "embedded"

    def test_partial(self, db: ThreadSafeConnection) -> None:
        source, _ = create_source(db, source_type="text", title="Note", content="hello world")
        # Add a second chunk manually so we have 2 total
        create_source_chunks(db, source["id"], ["extra chunk for partial test"])
        chunks = list_source_chunks(db, source["id"])
        assert len(chunks) >= 2
        # Only embed the first chunk
        db.execute(
            "INSERT INTO source_chunk_embeddings (chunk_id, source_id, content_hash, status, created_at) "
            "VALUES (?, ?, 'hash1', 'embedded', '2024-01-01T00:00:00')",
            (chunks[0]["id"], source["id"]),
        )
        db.commit()
        assert get_source_embedding_status(db, source["id"]) == "partial"

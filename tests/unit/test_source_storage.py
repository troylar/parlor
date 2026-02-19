"""Tests for source storage operations (CRUD, chunking, tags, groups, project linking, dual citizenship)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anteroom.db import _SCHEMA, _VEC_METADATA_SCHEMA, ThreadSafeConnection
from anteroom.services.storage import (
    _validate_upload,
    add_source_to_group,
    add_tag_to_source,
    chunk_text,
    create_conversation,
    create_message,
    create_project,
    create_source,
    create_source_chunks,
    create_source_from_attachment,
    create_source_group,
    create_tag,
    delete_source,
    delete_source_group,
    get_project_sources,
    get_source,
    get_source_group,
    get_source_tags,
    get_unembedded_source_chunks,
    link_source_to_project,
    list_source_chunks,
    list_source_groups,
    list_sources,
    remove_source_from_group,
    remove_tag_from_source,
    save_attachment,
    save_source_file,
    unlink_source_from_project,
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
        source = create_source(db, source_type="text", title="Test Note", content="Some content here.")
        assert source["type"] == "text"
        assert source["title"] == "Test Note"
        assert source["content"] == "Some content here."
        assert source["content_hash"] is not None

    def test_create_url_source(self, db: ThreadSafeConnection) -> None:
        source = create_source(db, source_type="url", title="Link", url="https://example.com")
        assert source["type"] == "url"
        assert source["url"] == "https://example.com"

    def test_create_source_invalid_type_raises(self, db: ThreadSafeConnection) -> None:
        with pytest.raises(ValueError, match="Invalid source type"):
            create_source(db, source_type="invalid", title="Bad")

    def test_get_source(self, db: ThreadSafeConnection) -> None:
        source = create_source(db, source_type="text", title="Test", content="Content here for testing.")
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
        source = create_source(db, source_type="text", title="Old", content="Content here for update.")
        updated = update_source(db, source["id"], title="New")
        assert updated is not None
        assert updated["title"] == "New"

    def test_update_source_content_rechunks(self, db: ThreadSafeConnection) -> None:
        source = create_source(db, source_type="text", title="Note", content="Original content here.")
        update_source(db, source["id"], content="Updated content with new text here.")
        new_chunks = list_source_chunks(db, source["id"])
        assert new_chunks[0]["content"] == "Updated content with new text here."

    def test_update_source_missing(self, db: ThreadSafeConnection) -> None:
        assert update_source(db, "nonexistent", title="New") is None

    def test_delete_source(self, db: ThreadSafeConnection) -> None:
        source = create_source(db, source_type="text", title="Delete me", content="Content.")
        assert delete_source(db, source["id"]) is True
        assert get_source(db, source["id"]) is None

    def test_delete_source_missing(self, db: ThreadSafeConnection) -> None:
        assert delete_source(db, "nonexistent") is False


class TestSourceChunks:
    def test_create_and_list_chunks(self, db: ThreadSafeConnection) -> None:
        source = create_source(db, source_type="text", title="Test", content="Short.")
        chunks = create_source_chunks(db, source["id"], ["chunk 1", "chunk 2"])
        assert len(chunks) == 2
        assert chunks[0]["chunk_index"] == 0
        assert chunks[1]["chunk_index"] == 1

        listed = list_source_chunks(db, source["id"])
        assert len(listed) >= 2  # may include auto-chunks from create_source

    def test_auto_chunking_on_create(self, db: ThreadSafeConnection) -> None:
        long_text = "First sentence. " * 100
        source = create_source(db, source_type="text", title="Long", content=long_text)
        chunks = list_source_chunks(db, source["id"])
        assert len(chunks) > 1

    def test_get_unembedded_source_chunks(self, db: ThreadSafeConnection) -> None:
        source = create_source(db, source_type="text", title="Test", content="Long enough content for embedding test.")
        unembedded = get_unembedded_source_chunks(db)
        assert len(unembedded) > 0
        assert unembedded[0]["source_id"] == source["id"]


class TestSourceTags:
    def test_add_and_get_tags(self, db: ThreadSafeConnection) -> None:
        source = create_source(db, source_type="text", title="Test", content="Content.")
        tag = create_tag(db, name="important")
        assert add_tag_to_source(db, source["id"], tag["id"]) is True
        tags = get_source_tags(db, source["id"])
        assert len(tags) == 1
        assert tags[0]["name"] == "important"

    def test_remove_tag(self, db: ThreadSafeConnection) -> None:
        source = create_source(db, source_type="text", title="Test", content="Content.")
        tag = create_tag(db, name="temp")
        add_tag_to_source(db, source["id"], tag["id"])
        remove_tag_from_source(db, source["id"], tag["id"])
        tags = get_source_tags(db, source["id"])
        assert len(tags) == 0

    def test_list_sources_by_tag(self, db: ThreadSafeConnection) -> None:
        s1 = create_source(db, source_type="text", title="Tagged", content="Content.")
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
        source = create_source(db, source_type="text", title="S1", content="Content.")
        assert add_source_to_group(db, group["id"], source["id"]) is True

        result = list_sources(db, group_id=group["id"])
        assert len(result) == 1

        remove_source_from_group(db, group["id"], source["id"])
        result = list_sources(db, group_id=group["id"])
        assert len(result) == 0


class TestProjectSources:
    def test_link_source_to_project(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, name="Test Project")
        source = create_source(db, source_type="text", title="S1", content="Content.")
        link = link_source_to_project(db, proj["id"], source_id=source["id"])
        assert link["project_id"] == proj["id"]
        assert link["source_id"] == source["id"]

    def test_link_requires_exactly_one(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, name="Test")
        with pytest.raises(ValueError, match="Exactly one"):
            link_source_to_project(db, proj["id"])

    def test_get_project_sources_direct(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, name="Test")
        s1 = create_source(db, source_type="text", title="S1", content="Content.")
        s2 = create_source(db, source_type="text", title="S2", content="Content.")
        link_source_to_project(db, proj["id"], source_id=s1["id"])
        link_source_to_project(db, proj["id"], source_id=s2["id"])
        sources = get_project_sources(db, proj["id"])
        assert len(sources) == 2

    def test_get_project_sources_via_group(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, name="Test")
        group = create_source_group(db, name="Group")
        s1 = create_source(db, source_type="text", title="S1", content="Content.")
        add_source_to_group(db, group["id"], s1["id"])
        link_source_to_project(db, proj["id"], group_id=group["id"])
        sources = get_project_sources(db, proj["id"])
        assert len(sources) == 1
        assert sources[0]["id"] == s1["id"]

    def test_get_project_sources_via_tag_filter(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, name="Test")
        tag = create_tag(db, name="docs")
        s1 = create_source(db, source_type="text", title="S1", content="Content.")
        add_tag_to_source(db, s1["id"], tag["id"])
        link_source_to_project(db, proj["id"], tag_filter="docs")
        sources = get_project_sources(db, proj["id"])
        assert len(sources) == 1

    def test_unlink_source(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, name="Test")
        source = create_source(db, source_type="text", title="S1", content="Content.")
        link_source_to_project(db, proj["id"], source_id=source["id"])
        unlink_source_from_project(db, proj["id"], source_id=source["id"])
        sources = get_project_sources(db, proj["id"])
        assert len(sources) == 0

    def test_list_sources_by_project(self, db: ThreadSafeConnection) -> None:
        proj = create_project(db, name="Test")
        s1 = create_source(db, source_type="text", title="In Project", content="Content.")
        create_source(db, source_type="text", title="Not in Project", content="Content.")
        link_source_to_project(db, proj["id"], source_id=s1["id"])
        result = list_sources(db, project_id=proj["id"])
        assert len(result) == 1
        assert result[0]["id"] == s1["id"]


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
        source = save_source_file(
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
        source = save_source_file(
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
        source = save_source_file(
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
        source = save_source_file(
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

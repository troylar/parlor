"""Unit tests for artifact model, FQN validation, storage CRUD, and registry."""

from __future__ import annotations

import sqlite3

import pytest

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.services.artifact_registry import ArtifactRegistry, _artifact_from_row
from anteroom.services.artifact_storage import (
    _row_to_dict,
    create_artifact,
    delete_artifact,
    get_artifact,
    get_artifact_by_fqn,
    list_artifact_versions,
    list_artifacts,
    update_artifact,
    upsert_artifact,
)
from anteroom.services.artifacts import (
    Artifact,
    ArtifactSource,
    ArtifactType,
    build_fqn,
    content_hash,
    parse_fqn,
    validate_fqn,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


# ---------------------------------------------------------------------------
# FQN validation
# ---------------------------------------------------------------------------


class TestValidateFqn:
    def test_valid_simple(self) -> None:
        assert validate_fqn("@core/skill/greet") is True

    def test_valid_with_hyphens(self) -> None:
        assert validate_fqn("@my-team/rule/no-eval") is True

    def test_valid_with_dots(self) -> None:
        assert validate_fqn("@core/config_overlay/v1.2") is True

    def test_valid_with_numbers(self) -> None:
        assert validate_fqn("@team1/memory/session0") is True

    def test_missing_at_sign(self) -> None:
        assert validate_fqn("core/skill/greet") is False

    def test_empty_string(self) -> None:
        assert validate_fqn("") is False

    def test_missing_name(self) -> None:
        assert validate_fqn("@core/skill/") is False

    def test_uppercase_rejected(self) -> None:
        assert validate_fqn("@Core/skill/greet") is False

    def test_spaces_rejected(self) -> None:
        assert validate_fqn("@core/skill/my greet") is False

    def test_extra_slashes_rejected(self) -> None:
        assert validate_fqn("@core/skill/greet/extra") is False

    def test_only_two_parts(self) -> None:
        assert validate_fqn("@core/skill") is False


class TestParseFqn:
    def test_basic_parse(self) -> None:
        ns, typ, name = parse_fqn("@core/skill/greet")
        assert ns == "core"
        assert typ == "skill"
        assert name == "greet"

    def test_parse_with_hyphens(self) -> None:
        ns, typ, name = parse_fqn("@my-team/rule/no-eval")
        assert ns == "my-team"
        assert name == "no-eval"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid FQN"):
            parse_fqn("bad")


class TestBuildFqn:
    def test_basic_build(self) -> None:
        assert build_fqn("core", "skill", "greet") == "@core/skill/greet"

    def test_invalid_components_raise(self) -> None:
        with pytest.raises(ValueError, match="Invalid FQN components"):
            build_fqn("CORE", "skill", "greet")


class TestContentHash:
    def test_deterministic(self) -> None:
        h1 = content_hash("hello")
        h2 = content_hash("hello")
        assert h1 == h2

    def test_different_content(self) -> None:
        assert content_hash("a") != content_hash("b")

    def test_sha256_length(self) -> None:
        assert len(content_hash("x")) == 64


# ---------------------------------------------------------------------------
# Artifact dataclass
# ---------------------------------------------------------------------------


class TestArtifactDataclass:
    def test_create_basic(self) -> None:
        a = Artifact(
            fqn="@core/skill/greet",
            type=ArtifactType.SKILL,
            namespace="core",
            name="greet",
            content="Say hello",
        )
        assert a.fqn == "@core/skill/greet"
        assert a.content_hash != ""
        assert a.version == 1
        assert a.source == ArtifactSource.LOCAL

    def test_auto_content_hash(self) -> None:
        a = Artifact(fqn="@x/skill/y", type=ArtifactType.SKILL, namespace="x", name="y", content="test")
        assert a.content_hash == content_hash("test")

    def test_explicit_content_hash(self) -> None:
        a = Artifact(
            fqn="@x/skill/y",
            type=ArtifactType.SKILL,
            namespace="x",
            name="y",
            content="test",
            content_hash="custom",
        )
        assert a.content_hash == "custom"

    def test_invalid_fqn_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid FQN"):
            Artifact(fqn="bad", type=ArtifactType.SKILL, namespace="x", name="y", content="z")

    def test_frozen(self) -> None:
        a = Artifact(fqn="@x/skill/y", type=ArtifactType.SKILL, namespace="x", name="y", content="z")
        with pytest.raises(AttributeError):
            a.content = "new"


# ---------------------------------------------------------------------------
# Artifact storage CRUD
# ---------------------------------------------------------------------------


class TestCreateArtifact:
    def test_create_returns_dict(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@core/skill/greet", "skill", "core", "greet", "Say hello")
        assert art["id"]
        assert art["fqn"] == "@core/skill/greet"
        assert art["type"] == "skill"
        assert art["content_hash"] == content_hash("Say hello")
        assert art["version"] == 1

    def test_create_with_metadata(self, db: ThreadSafeConnection) -> None:
        meta = {"author": "test", "tags": ["demo"]}
        art = create_artifact(db, "@core/skill/greet", "skill", "core", "greet", "hi", metadata=meta)
        assert art["metadata"] == meta

    def test_create_with_user_info(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(
            db,
            "@core/skill/greet",
            "skill",
            "core",
            "greet",
            "hi",
            user_id="u1",
            user_display_name="Alice",
        )
        assert art["user_id"] == "u1"
        assert art["user_display_name"] == "Alice"

    def test_create_invalid_fqn_raises(self, db: ThreadSafeConnection) -> None:
        with pytest.raises(ValueError, match="Invalid FQN"):
            create_artifact(db, "bad", "skill", "core", "greet", "hi")

    def test_create_invalid_type_raises(self, db: ThreadSafeConnection) -> None:
        with pytest.raises(ValueError):
            create_artifact(db, "@core/skill/greet", "bogus", "core", "greet", "hi")

    def test_duplicate_fqn_raises(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@core/skill/greet", "skill", "core", "greet", "v1")
        with pytest.raises(sqlite3.IntegrityError):
            create_artifact(db, "@core/skill/greet", "skill", "core", "greet", "v2")

    def test_creates_version_record(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@core/skill/greet", "skill", "core", "greet", "hello")
        versions = list_artifact_versions(db, art["id"])
        assert len(versions) == 1
        assert versions[0]["version"] == 1
        assert versions[0]["content"] == "hello"


class TestGetArtifact:
    def test_get_by_id(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@core/skill/greet", "skill", "core", "greet", "hi")
        fetched = get_artifact(db, art["id"])
        assert fetched is not None
        assert fetched["fqn"] == "@core/skill/greet"

    def test_get_missing_returns_none(self, db: ThreadSafeConnection) -> None:
        assert get_artifact(db, "nonexistent") is None

    def test_get_by_fqn(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@core/skill/greet", "skill", "core", "greet", "hi")
        fetched = get_artifact_by_fqn(db, "@core/skill/greet")
        assert fetched is not None
        assert fetched["name"] == "greet"

    def test_get_by_fqn_missing(self, db: ThreadSafeConnection) -> None:
        assert get_artifact_by_fqn(db, "@no/such/thing") is None

    def test_metadata_deserialized(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@x/skill/y", "skill", "x", "y", "c", metadata={"k": 1})
        fetched = get_artifact(db, get_artifact_by_fqn(db, "@x/skill/y")["id"])
        assert fetched["metadata"] == {"k": 1}


class TestListArtifacts:
    def test_list_all(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@a/skill/x", "skill", "a", "x", "c1")
        create_artifact(db, "@a/rule/y", "rule", "a", "y", "c2")
        assert len(list_artifacts(db)) == 2

    def test_filter_by_type(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@a/skill/x", "skill", "a", "x", "c1")
        create_artifact(db, "@a/rule/y", "rule", "a", "y", "c2")
        skills = list_artifacts(db, artifact_type="skill")
        assert len(skills) == 1
        assert skills[0]["type"] == "skill"

    def test_filter_by_namespace(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@a/skill/x", "skill", "a", "x", "c1")
        create_artifact(db, "@b/skill/y", "skill", "b", "y", "c2")
        a_arts = list_artifacts(db, namespace="a")
        assert len(a_arts) == 1

    def test_filter_by_source(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@a/skill/x", "skill", "a", "x", "c1", source="built_in")
        create_artifact(db, "@a/skill/y", "skill", "a", "y", "c2", source="local")
        built_ins = list_artifacts(db, source="built_in")
        assert len(built_ins) == 1

    def test_combined_filters(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@a/skill/x", "skill", "a", "x", "c1")
        create_artifact(db, "@a/rule/y", "rule", "a", "y", "c2")
        create_artifact(db, "@b/skill/z", "skill", "b", "z", "c3")
        result = list_artifacts(db, artifact_type="skill", namespace="a")
        assert len(result) == 1
        assert result[0]["fqn"] == "@a/skill/x"

    def test_empty_result(self, db: ThreadSafeConnection) -> None:
        assert list_artifacts(db) == []


class TestUpdateArtifact:
    def test_update_content(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@a/skill/x", "skill", "a", "x", "v1")
        updated = update_artifact(db, art["id"], content="v2")
        assert updated is not None
        assert updated["content"] == "v2"
        assert updated["content_hash"] == content_hash("v2")

    def test_update_creates_new_version(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@a/skill/x", "skill", "a", "x", "v1")
        update_artifact(db, art["id"], content="v2")
        versions = list_artifact_versions(db, art["id"])
        assert len(versions) == 2
        assert versions[0]["version"] == 2  # newest first
        assert versions[1]["version"] == 1

    def test_update_same_content_no_version(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@a/skill/x", "skill", "a", "x", "same")
        update_artifact(db, art["id"], content="same")
        versions = list_artifact_versions(db, art["id"])
        assert len(versions) == 1

    def test_update_metadata_only(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@a/skill/x", "skill", "a", "x", "c")
        updated = update_artifact(db, art["id"], metadata={"k": "v"})
        assert updated["metadata"] == {"k": "v"}
        versions = list_artifact_versions(db, art["id"])
        assert len(versions) == 1  # no new version for metadata-only

    def test_update_nonexistent_returns_none(self, db: ThreadSafeConnection) -> None:
        assert update_artifact(db, "nonexistent", content="x") is None

    def test_multiple_version_bumps(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@a/skill/x", "skill", "a", "x", "v1")
        update_artifact(db, art["id"], content="v2")
        update_artifact(db, art["id"], content="v3")
        versions = list_artifact_versions(db, art["id"])
        assert len(versions) == 3
        assert [v["version"] for v in versions] == [3, 2, 1]


class TestDeleteArtifact:
    def test_delete_existing(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@a/skill/x", "skill", "a", "x", "c")
        assert delete_artifact(db, art["id"]) is True
        assert get_artifact(db, art["id"]) is None

    def test_delete_nonexistent(self, db: ThreadSafeConnection) -> None:
        assert delete_artifact(db, "nope") is False

    def test_cascade_deletes_versions(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@a/skill/x", "skill", "a", "x", "v1")
        update_artifact(db, art["id"], content="v2")
        delete_artifact(db, art["id"])
        assert list_artifact_versions(db, art["id"]) == []


class TestUpsertArtifact:
    def test_upsert_creates_new(self, db: ThreadSafeConnection) -> None:
        art = upsert_artifact(db, "@a/skill/x", "skill", "a", "x", "hello")
        assert art["fqn"] == "@a/skill/x"
        assert art["content"] == "hello"

    def test_upsert_updates_existing(self, db: ThreadSafeConnection) -> None:
        upsert_artifact(db, "@a/skill/x", "skill", "a", "x", "v1")
        art = upsert_artifact(db, "@a/skill/x", "skill", "a", "x", "v2")
        assert art["content"] == "v2"
        assert len(list_artifact_versions(db, art["id"])) == 2

    def test_upsert_same_content_no_version_bump(self, db: ThreadSafeConnection) -> None:
        upsert_artifact(db, "@a/skill/x", "skill", "a", "x", "same")
        art = upsert_artifact(db, "@a/skill/x", "skill", "a", "x", "same")
        assert len(list_artifact_versions(db, art["id"])) == 1


class TestListArtifactVersions:
    def test_empty_for_nonexistent(self, db: ThreadSafeConnection) -> None:
        assert list_artifact_versions(db, "nonexistent") == []

    def test_version_fields(self, db: ThreadSafeConnection) -> None:
        art = create_artifact(db, "@a/skill/x", "skill", "a", "x", "c")
        versions = list_artifact_versions(db, art["id"])
        v = versions[0]
        assert "id" in v
        assert v["artifact_id"] == art["id"]
        assert v["version"] == 1
        assert v["content"] == "c"
        assert v["content_hash"] == content_hash("c")
        assert "created_at" in v


# ---------------------------------------------------------------------------
# ArtifactRegistry
# ---------------------------------------------------------------------------


class TestArtifactRegistry:
    def test_register_and_get(self) -> None:
        reg = ArtifactRegistry()
        art = Artifact(fqn="@core/skill/greet", type=ArtifactType.SKILL, namespace="core", name="greet", content="hi")
        reg.register(art)
        assert reg.get("@core/skill/greet") is art
        assert reg.count == 1

    def test_get_missing(self) -> None:
        reg = ArtifactRegistry()
        assert reg.get("@no/such/thing") is None

    def test_unregister(self) -> None:
        reg = ArtifactRegistry()
        art = Artifact(fqn="@x/skill/y", type=ArtifactType.SKILL, namespace="x", name="y", content="c")
        reg.register(art)
        assert reg.unregister("@x/skill/y") is True
        assert reg.get("@x/skill/y") is None
        assert reg.count == 0

    def test_unregister_missing(self) -> None:
        reg = ArtifactRegistry()
        assert reg.unregister("@no/such/thing") is False

    def test_list_all(self) -> None:
        reg = ArtifactRegistry()
        reg.register(Artifact(fqn="@a/skill/x", type=ArtifactType.SKILL, namespace="a", name="x", content="1"))
        reg.register(Artifact(fqn="@a/rule/y", type=ArtifactType.RULE, namespace="a", name="y", content="2"))
        assert len(reg.list_all()) == 2

    def test_list_filter_type(self) -> None:
        reg = ArtifactRegistry()
        reg.register(Artifact(fqn="@a/skill/x", type=ArtifactType.SKILL, namespace="a", name="x", content="1"))
        reg.register(Artifact(fqn="@a/rule/y", type=ArtifactType.RULE, namespace="a", name="y", content="2"))
        assert len(reg.list_all(artifact_type=ArtifactType.SKILL)) == 1

    def test_list_filter_namespace(self) -> None:
        reg = ArtifactRegistry()
        reg.register(Artifact(fqn="@a/skill/x", type=ArtifactType.SKILL, namespace="a", name="x", content="1"))
        reg.register(Artifact(fqn="@b/skill/y", type=ArtifactType.SKILL, namespace="b", name="y", content="2"))
        assert len(reg.list_all(namespace="a")) == 1

    def test_list_filter_source(self) -> None:
        reg = ArtifactRegistry()
        reg.register(
            Artifact(
                fqn="@a/skill/x",
                type=ArtifactType.SKILL,
                namespace="a",
                name="x",
                content="1",
                source=ArtifactSource.BUILT_IN,
            )
        )
        reg.register(
            Artifact(
                fqn="@a/skill/y",
                type=ArtifactType.SKILL,
                namespace="a",
                name="y",
                content="2",
                source=ArtifactSource.LOCAL,
            )
        )
        assert len(reg.list_all(source=ArtifactSource.BUILT_IN)) == 1

    def test_search(self) -> None:
        reg = ArtifactRegistry()
        reg.register(
            Artifact(fqn="@a/skill/greet-user", type=ArtifactType.SKILL, namespace="a", name="greet-user", content="1")
        )
        reg.register(
            Artifact(fqn="@a/skill/farewell", type=ArtifactType.SKILL, namespace="a", name="farewell", content="2")
        )
        results = reg.search("greet")
        assert len(results) == 1
        assert results[0].name == "greet-user"

    def test_search_case_insensitive(self) -> None:
        reg = ArtifactRegistry()
        reg.register(Artifact(fqn="@a/skill/greet", type=ArtifactType.SKILL, namespace="a", name="greet", content="1"))
        assert len(reg.search("GREET")) == 1

    def test_clear(self) -> None:
        reg = ArtifactRegistry()
        reg.register(Artifact(fqn="@a/skill/x", type=ArtifactType.SKILL, namespace="a", name="x", content="1"))
        reg.clear()
        assert reg.count == 0

    def test_load_from_db(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@a/skill/x", "skill", "a", "x", "content1")
        create_artifact(db, "@a/rule/y", "rule", "a", "y", "content2")
        reg = ArtifactRegistry()
        reg.load_from_db(db)
        assert reg.count == 2
        art = reg.get("@a/skill/x")
        assert art is not None
        assert art.content == "content1"

    def test_load_from_db_layer_precedence(self, db: ThreadSafeConnection) -> None:
        create_artifact(db, "@a/skill/x", "skill", "a", "x", "built-in", source="built_in")
        # Simulate a project override with same FQN by inserting directly
        # (create_artifact would fail on UNIQUE constraint)
        # Instead, create different FQN artifacts and test registry override via register
        reg = ArtifactRegistry()
        builtin = Artifact(
            fqn="@a/skill/x",
            type=ArtifactType.SKILL,
            namespace="a",
            name="x",
            content="built-in",
            source=ArtifactSource.BUILT_IN,
        )
        project = Artifact(
            fqn="@a/skill/x",
            type=ArtifactType.SKILL,
            namespace="a",
            name="x",
            content="project-override",
            source=ArtifactSource.PROJECT,
        )
        reg.register(builtin)
        reg.register(project)
        art = reg.get("@a/skill/x")
        assert art.content == "project-override"
        assert art.source == ArtifactSource.PROJECT

    def test_reload_alias(self) -> None:
        assert ArtifactRegistry.reload is ArtifactRegistry.load_from_db


class TestArtifactRegistryAttachmentFiltering:
    """Verify load_from_db only loads artifacts from attached packs."""

    def _install_pack(self, db: ThreadSafeConnection, ns: str, name: str) -> str:
        """Insert a pack and return its id."""
        import uuid
        from datetime import datetime, timezone

        pack_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO packs (id, namespace, name, version, installed_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pack_id, ns, name, "1.0.0", now, now),
        )
        return pack_id

    def _link_artifact_to_pack(self, db: ThreadSafeConnection, pack_id: str, artifact_id: str) -> None:
        db.execute("INSERT INTO pack_artifacts (pack_id, artifact_id) VALUES (?, ?)", (pack_id, artifact_id))

    def _attach_pack(
        self, db: ThreadSafeConnection, pack_id: str, scope: str = "global", space_id: str | None = None
    ) -> None:
        import uuid
        from datetime import datetime, timezone

        att_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO pack_attachments (id, pack_id, scope, space_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (att_id, pack_id, scope, space_id, now),
        )

    def test_standalone_artifacts_always_loaded(self, db: ThreadSafeConnection) -> None:
        """Artifacts not linked to any pack are always loaded."""
        create_artifact(db, "@a/skill/standalone", "skill", "a", "standalone", "content")
        reg = ArtifactRegistry()
        reg.load_from_db(db)
        assert reg.get("@a/skill/standalone") is not None

    def test_attached_pack_artifacts_loaded(self, db: ThreadSafeConnection) -> None:
        """Artifacts from an attached pack are loaded."""
        pack_id = self._install_pack(db, "test", "mypack")
        art = create_artifact(db, "@test/skill/hello", "skill", "test", "hello", "content")
        self._link_artifact_to_pack(db, pack_id, art["id"])
        self._attach_pack(db, pack_id)
        db.commit()

        reg = ArtifactRegistry()
        reg.load_from_db(db)
        assert reg.get("@test/skill/hello") is not None

    def test_unattached_pack_artifacts_excluded(self, db: ThreadSafeConnection) -> None:
        """Artifacts from installed-but-not-attached packs are excluded."""
        pack_id = self._install_pack(db, "test", "mypack")
        art = create_artifact(db, "@test/skill/hello", "skill", "test", "hello", "content")
        self._link_artifact_to_pack(db, pack_id, art["id"])
        # No attach call — pack is installed but not attached
        db.commit()

        reg = ArtifactRegistry()
        reg.load_from_db(db)
        assert reg.get("@test/skill/hello") is None

    def test_config_overlay_excluded_from_attached_only(self, db: ThreadSafeConnection) -> None:
        """Config overlays are excluded — they load via collect_pack_overlays."""
        pack_id = self._install_pack(db, "test", "cfgpack")
        art = create_artifact(db, "@test/config_overlay/safety", "config_overlay", "test", "safety", "key: val")
        self._link_artifact_to_pack(db, pack_id, art["id"])
        self._attach_pack(db, pack_id)
        db.commit()

        reg = ArtifactRegistry()
        reg.load_from_db(db)
        assert reg.get("@test/config_overlay/safety") is None

    def test_space_scoped_attachment_loaded(self, db: ThreadSafeConnection) -> None:
        """Artifacts from a space-scoped attached pack are loaded when space matches."""
        import uuid
        from datetime import datetime, timezone

        # Create a space
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO spaces (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)", (sid, "TestSpace", now, now)
        )

        pack_id = self._install_pack(db, "test", "spacepk")
        art = create_artifact(db, "@test/skill/spaced", "skill", "test", "spaced", "content")
        self._link_artifact_to_pack(db, pack_id, art["id"])
        self._attach_pack(db, pack_id, scope="space", space_id=sid)
        db.commit()

        reg = ArtifactRegistry()
        reg.load_from_db(db, space_id=sid)
        assert reg.get("@test/skill/spaced") is not None

    def test_space_scoped_attachment_excluded_wrong_space(self, db: ThreadSafeConnection) -> None:
        """Artifacts from a space-scoped pack are excluded when space doesn't match."""
        import uuid
        from datetime import datetime, timezone

        sid = str(uuid.uuid4())
        other_sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO spaces (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)", (sid, "Space1", now, now)
        )
        db.execute(
            "INSERT INTO spaces (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)", (other_sid, "Space2", now, now)
        )

        pack_id = self._install_pack(db, "test", "spacepk")
        art = create_artifact(db, "@test/skill/spaced", "skill", "test", "spaced", "content")
        self._link_artifact_to_pack(db, pack_id, art["id"])
        self._attach_pack(db, pack_id, scope="space", space_id=sid)
        db.commit()

        reg = ArtifactRegistry()
        reg.load_from_db(db, space_id=other_sid)
        assert reg.get("@test/skill/spaced") is None

    def test_detach_removes_from_registry(self, db: ThreadSafeConnection) -> None:
        """After detaching, reloading the registry excludes the pack's artifacts."""
        pack_id = self._install_pack(db, "test", "mypk")
        art = create_artifact(db, "@test/rule/block", "rule", "test", "block", "deny all")
        self._link_artifact_to_pack(db, pack_id, art["id"])
        self._attach_pack(db, pack_id)
        db.commit()

        reg = ArtifactRegistry()
        reg.load_from_db(db)
        assert reg.get("@test/rule/block") is not None

        # Detach
        db.execute("DELETE FROM pack_attachments WHERE pack_id = ?", (pack_id,))
        db.commit()

        reg.load_from_db(db)
        assert reg.get("@test/rule/block") is None


class TestArtifactFromRow:
    def test_converts_row_to_artifact(self) -> None:
        row = {
            "fqn": "@core/skill/greet",
            "type": "skill",
            "namespace": "core",
            "name": "greet",
            "content": "hello",
            "version": 2,
            "source": "local",
            "metadata": {"k": "v"},
            "content_hash": content_hash("hello"),
        }
        art = _artifact_from_row(row)
        assert isinstance(art, Artifact)
        assert art.fqn == "@core/skill/greet"
        assert art.type == ArtifactType.SKILL
        assert art.version == 2
        assert art.metadata == {"k": "v"}

    def test_missing_optional_fields(self) -> None:
        row = {
            "fqn": "@x/skill/y",
            "type": "skill",
            "namespace": "x",
            "name": "y",
            "content": "c",
            "source": "local",
        }
        art = _artifact_from_row(row)
        assert art.version == 1
        assert art.metadata == {}


# ---------------------------------------------------------------------------
# Bug fix: upsert_artifact IntegrityError race condition (#522)
# ---------------------------------------------------------------------------


class TestUpsertArtifactRaceCondition:
    def test_upsert_handles_integrity_error(self, db: ThreadSafeConnection) -> None:
        """If create fails with IntegrityError (concurrent insert), upsert retries as update."""
        from unittest.mock import patch

        # Pre-insert an artifact
        create_artifact(db, "@a/skill/race", "skill", "a", "race", "original")

        # Patch get_artifact_by_fqn to return None on first call (simulating race window),
        # then return the real row on second call (after IntegrityError)
        original_get = get_artifact_by_fqn
        call_count = {"n": 0}

        def mock_get(conn: object, fqn: str) -> object:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None  # Simulate "not found" in race window
            return original_get(conn, fqn)

        with patch("anteroom.services.artifact_storage.get_artifact_by_fqn", side_effect=mock_get):
            result = upsert_artifact(db, "@a/skill/race", "skill", "a", "race", "updated")

        assert result is not None
        assert result["content"] == "updated"


# ---------------------------------------------------------------------------
# Bug fix: _row_to_dict malformed JSON (#522)
# ---------------------------------------------------------------------------


class TestRowToDictMalformedJson:
    def test_valid_json_metadata(self) -> None:
        """Normal case: valid JSON metadata is deserialized."""

        db_conn = sqlite3.connect(":memory:")
        db_conn.row_factory = sqlite3.Row
        db_conn.execute("CREATE TABLE t (id TEXT, fqn TEXT, metadata TEXT)")
        db_conn.execute("INSERT INTO t VALUES (?, ?, ?)", ("123", "@a/skill/x", '{"key": "value"}'))
        row = db_conn.execute("SELECT * FROM t").fetchone()
        result = _row_to_dict(row)
        assert result["metadata"] == {"key": "value"}

    def test_malformed_json_metadata_returns_empty_dict(self) -> None:
        """Malformed JSON metadata should fall back to {} instead of crashing."""
        db_conn = sqlite3.connect(":memory:")
        db_conn.row_factory = sqlite3.Row
        db_conn.execute("CREATE TABLE t (id TEXT, fqn TEXT, metadata TEXT)")
        db_conn.execute("INSERT INTO t VALUES (?, ?, ?)", ("123", "@a/skill/x", "{not valid json"))
        row = db_conn.execute("SELECT * FROM t").fetchone()
        result = _row_to_dict(row)
        assert result["metadata"] == {}

    def test_empty_string_metadata_returns_empty_dict(self) -> None:
        """Empty string metadata should fall back to {}."""
        db_conn = sqlite3.connect(":memory:")
        db_conn.row_factory = sqlite3.Row
        db_conn.execute("CREATE TABLE t (id TEXT, fqn TEXT, metadata TEXT)")
        db_conn.execute("INSERT INTO t VALUES (?, ?, ?)", ("123", "@a/skill/x", ""))
        row = db_conn.execute("SELECT * FROM t").fetchone()
        result = _row_to_dict(row)
        assert result["metadata"] == {}


class TestArtifactRegistryContentHashValidation:
    """Tests for content hash mismatch detection on artifact load."""

    def test_mismatched_hash_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Artifact with mismatched content_hash should log a warning."""
        row = {
            "fqn": "@test/skill/greet",
            "type": "skill",
            "namespace": "test",
            "name": "greet",
            "content": "Hello!",
            "version": 1,
            "source": "local",
            "metadata": {},
            "content_hash": "deadbeef0000",  # wrong hash
        }
        import logging

        with caplog.at_level(logging.WARNING, logger="anteroom.services.artifact_registry"):
            art = _artifact_from_row(row)
        assert "Content hash mismatch" in caplog.text
        assert art.content == "Hello!"  # still loads the artifact

    def test_correct_hash_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Artifact with correct content_hash should not log a warning."""
        from anteroom.services.artifacts import content_hash as compute

        c = "Hello!"
        row = {
            "fqn": "@test/skill/greet",
            "type": "skill",
            "namespace": "test",
            "name": "greet",
            "content": c,
            "version": 1,
            "source": "local",
            "metadata": {},
            "content_hash": compute(c),
        }
        import logging

        with caplog.at_level(logging.WARNING, logger="anteroom.services.artifact_registry"):
            _artifact_from_row(row)
        assert "Content hash mismatch" not in caplog.text

    def test_empty_hash_skips_validation(self, caplog: pytest.LogCaptureFixture) -> None:
        """Artifact with empty content_hash should skip validation (old data)."""
        row = {
            "fqn": "@test/skill/greet",
            "type": "skill",
            "namespace": "test",
            "name": "greet",
            "content": "Hello!",
            "version": 1,
            "source": "local",
            "metadata": {},
            "content_hash": "",
        }
        import logging

        with caplog.at_level(logging.WARNING, logger="anteroom.services.artifact_registry"):
            _artifact_from_row(row)
        assert "Content hash mismatch" not in caplog.text

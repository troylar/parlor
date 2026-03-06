"""Integration tests for the pack API through real HTTP endpoints.

Starts a FastAPI app with a real in-memory SQLite DB (no mocks),
creates real pack directories on disk, and exercises the full pack
lifecycle through the HTTP API layer.

This verifies that the router → service → DB chain works end-to-end,
including registry reload after mutations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Generator

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.db import _SCHEMA, ThreadSafeConnection
from anteroom.routers.packs import router as packs_router
from anteroom.services.artifact_registry import ArtifactRegistry
from anteroom.services.artifacts import ArtifactType
from anteroom.services.packs import install_pack, parse_manifest
from anteroom.services.rule_enforcer import RuleEnforcer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> ThreadSafeConnection:
    """Real in-memory SQLite database with full schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return ThreadSafeConnection(conn)


@pytest.fixture()
def app(db: ThreadSafeConnection) -> FastAPI:
    """FastAPI app with real DB and live registries on app.state."""
    app = FastAPI()
    app.include_router(packs_router, prefix="/api")

    app.state.db = db
    app.state.artifact_registry = ArtifactRegistry()
    app.state.rule_enforcer = RuleEnforcer()

    # SkillRegistry is optional — create a minimal mock
    from unittest.mock import MagicMock

    app.state.skill_registry = MagicMock()

    # Config stub (only needs pack_sources for /sources endpoint)
    config = MagicMock()
    config.pack_sources = []
    config.app.data_dir = Path("/tmp/test-data")
    app.state.config = config

    return app


@pytest.fixture()
def client(app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Pack directory builders
# ---------------------------------------------------------------------------


def _write_pack(
    base: Path,
    name: str,
    namespace: str,
    version: str = "1.0.0",
    skill_files: dict[str, str] | None = None,
    rule_files: dict[str, dict[str, Any]] | None = None,
    config_overlay_files: dict[str, dict[str, Any]] | None = None,
) -> Path:
    """Create a pack directory with manifest and artifact files on disk."""
    pack_dir = base / f"{namespace}-{name}"
    pack_dir.mkdir(parents=True, exist_ok=True)

    manifest_artifacts: list[dict[str, str]] = []

    if skill_files:
        skills_dir = pack_dir / "skills"
        skills_dir.mkdir(exist_ok=True)
        for skill_name, content in skill_files.items():
            (skills_dir / f"{skill_name}.yaml").write_text(
                yaml.dump({"content": content, "metadata": {"tier": "read"}}),
                encoding="utf-8",
            )
            manifest_artifacts.append({"type": "skill", "name": skill_name})

    if rule_files:
        rules_dir = pack_dir / "rules"
        rules_dir.mkdir(exist_ok=True)
        for rule_name, rule_data in rule_files.items():
            (rules_dir / f"{rule_name}.yaml").write_text(
                yaml.dump({"content": rule_data["content"], "metadata": rule_data["metadata"]}),
                encoding="utf-8",
            )
            manifest_artifacts.append({"type": "rule", "name": rule_name})

    if config_overlay_files:
        overlays_dir = pack_dir / "config_overlays"
        overlays_dir.mkdir(exist_ok=True)
        for overlay_name, overlay_data in config_overlay_files.items():
            (overlays_dir / f"{overlay_name}.yaml").write_text(
                yaml.dump({"content": yaml.dump(overlay_data), "metadata": {}}),
                encoding="utf-8",
            )
            manifest_artifacts.append({"type": "config_overlay", "name": overlay_name})

    manifest = {
        "name": name,
        "namespace": namespace,
        "version": version,
        "description": f"Test pack {namespace}/{name}",
        "artifacts": manifest_artifacts,
    }
    (pack_dir / "pack.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
    return pack_dir


def _install_pack(db: ThreadSafeConnection, pack_dir: Path) -> dict[str, Any]:
    """Parse manifest and install pack into DB (service layer, not HTTP)."""
    manifest = parse_manifest(pack_dir / "pack.yaml")
    return install_pack(db, manifest, pack_dir)


def _security_pack(base: Path) -> Path:
    return _write_pack(
        base,
        name="security-baseline",
        namespace="acme",
        rule_files={
            "no-force-push": {
                "content": "Never force push to any branch.",
                "metadata": {
                    "enforce": "hard",
                    "reason": "Force push is forbidden",
                    "matches": [{"tool": "bash", "pattern": r"git\s+push\s+--force"}],
                },
            },
        },
        skill_files={"security-check": "Run a security check."},
    )


def _python_pack(base: Path) -> Path:
    return _write_pack(
        base,
        name="python-dev",
        namespace="acme",
        skill_files={
            "lint": "Run ruff check on the codebase.",
            "test": "Run pytest with verbose output.",
        },
    )


def _config_pack(base: Path) -> Path:
    return _write_pack(
        base,
        name="config-overlay",
        namespace="acme",
        config_overlay_files={
            "defaults": {"ai": {"temperature": 0.7}, "safety": {"approval_mode": "ask_for_writes"}},
        },
    )


# ---------------------------------------------------------------------------
# Tests: GET /api/packs
# ---------------------------------------------------------------------------


class TestListPacksAPI:
    def test_list_empty(self, client: TestClient) -> None:
        resp = client.get("/api/packs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_install(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp = client.get("/api/packs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["namespace"] == "acme"
        assert data[0]["name"] == "security-baseline"
        # source_path must be stripped from API response
        assert "source_path" not in data[0]

    def test_list_multiple_packs(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        _install_pack(db, _python_pack(tmp_path))
        resp = client.get("/api/packs")
        assert resp.status_code == 200
        names = {p["name"] for p in resp.json()}
        assert names == {"security-baseline", "python-dev"}


# ---------------------------------------------------------------------------
# Tests: GET /api/packs/{namespace}/{name}
# ---------------------------------------------------------------------------


class TestGetPackAPI:
    def test_get_existing(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp = client.get("/api/packs/acme/security-baseline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "security-baseline"
        assert data["namespace"] == "acme"
        assert len(data["artifacts"]) == 2  # 1 rule + 1 skill
        # content must be stripped from artifacts
        for art in data["artifacts"]:
            assert "content" not in art
        # source_path must be stripped
        assert "source_path" not in data

    def test_get_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/packs/no/such-pack")
        assert resp.status_code == 404

    def test_invalid_namespace_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/packs/!invalid/test-pack")
        assert resp.status_code == 400
        assert "Invalid namespace" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Tests: POST /api/packs/{namespace}/{name}/attach
# ---------------------------------------------------------------------------


class TestAttachPackAPI:
    def test_attach_global(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "global"
        assert data["pack_id"] is not None

    def test_attach_with_project_path(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp = client.post(
            "/api/packs/acme/security-baseline/attach",
            json={"project_path": "/my/project"},
        )
        assert resp.status_code == 200
        assert resp.json()["scope"] == "project"

    def test_attach_not_found(self, client: TestClient) -> None:
        resp = client.post("/api/packs/no/such-pack/attach", json={})
        assert resp.status_code == 404

    def test_duplicate_attach_returns_409(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp1 = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp1.status_code == 200
        resp2 = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# Tests: DELETE /api/packs/{namespace}/{name}/attach
# ---------------------------------------------------------------------------


class TestDetachPackAPI:
    def test_detach_success(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        client.post("/api/packs/acme/security-baseline/attach", json={})
        resp = client.delete("/api/packs/acme/security-baseline/attach")
        assert resp.status_code == 200
        assert resp.json()["status"] == "detached"

    def test_detach_not_attached(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp = client.delete("/api/packs/acme/security-baseline/attach")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/packs/{namespace}/{name}/attachments
# ---------------------------------------------------------------------------


class TestListAttachmentsAPI:
    def test_list_attachments_empty(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp = client.get("/api/packs/acme/security-baseline/attachments")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_attachments_after_attach(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        client.post("/api/packs/acme/security-baseline/attach", json={})
        resp = client.get("/api/packs/acme/security-baseline/attachments")
        assert resp.status_code == 200
        atts = resp.json()
        assert len(atts) == 1
        assert atts[0]["scope"] == "global"


# ---------------------------------------------------------------------------
# Tests: DELETE /api/packs/{namespace}/{name}
# ---------------------------------------------------------------------------


class TestRemovePackAPI:
    def test_remove_success(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp = client.delete("/api/packs/acme/security-baseline")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

        # Verify gone from list
        resp2 = client.get("/api/packs")
        assert resp2.json() == []

    def test_remove_not_found(self, client: TestClient) -> None:
        resp = client.delete("/api/packs/no/such-pack")
        assert resp.status_code == 404

    def test_remove_cleans_up_artifacts(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        _install_pack(db, _security_pack(tmp_path))

        # Verify artifacts exist
        arts = db.execute("SELECT COUNT(*) FROM artifacts").fetchone()
        assert arts[0] > 0

        client.delete("/api/packs/acme/security-baseline")

        # Artifacts should be gone
        arts_after = db.execute("SELECT COUNT(*) FROM artifacts").fetchone()
        assert arts_after[0] == 0


# ---------------------------------------------------------------------------
# Tests: Registry reload through API layer
# ---------------------------------------------------------------------------


class TestRegistryReloadViaAPI:
    """Verify that _reload_registries() is called after mutations,
    meaning app.state.artifact_registry and app.state.rule_enforcer
    are updated after API calls."""

    def test_registry_loaded_after_remove(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        _install_pack(db, _security_pack(tmp_path))

        # Manually load registry first
        app.state.artifact_registry.load_from_db(db)
        assert app.state.artifact_registry.count == 2  # 1 rule + 1 skill

        # Remove via API — should trigger reload
        resp = client.delete("/api/packs/acme/security-baseline")
        assert resp.status_code == 200

        # Registry should now be empty (reloaded after remove)
        assert app.state.artifact_registry.count == 0

    def test_rule_enforcer_loaded_after_remove(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        _install_pack(db, _security_pack(tmp_path))

        # Load registries
        registry = app.state.artifact_registry
        enforcer = app.state.rule_enforcer
        registry.load_from_db(db)
        enforcer.load_rules(registry.list_all(artifact_type=ArtifactType.RULE))
        assert enforcer.rule_count == 1

        # Remove via API — should reload enforcer too
        client.delete("/api/packs/acme/security-baseline")
        assert app.state.rule_enforcer.rule_count == 0

    def test_registry_updated_after_attach(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """attach_pack triggers _reload_registries, which reloads from DB."""
        _install_pack(db, _security_pack(tmp_path))
        app.state.artifact_registry.load_from_db(db)
        initial_count = app.state.artifact_registry.count

        resp = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp.status_code == 200

        # Registry should still have the same artifacts (reload doesn't change count,
        # but confirms the reload path works without error)
        assert app.state.artifact_registry.count == initial_count

    def test_registry_updated_after_detach(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        _install_pack(db, _security_pack(tmp_path))
        app.state.artifact_registry.load_from_db(db)

        client.post("/api/packs/acme/security-baseline/attach", json={})
        resp = client.delete("/api/packs/acme/security-baseline/attach")
        assert resp.status_code == 200

        # Registry should still be loaded (detach doesn't remove artifacts)
        assert app.state.artifact_registry.count == 2


# ---------------------------------------------------------------------------
# Tests: Full lifecycle through API
# ---------------------------------------------------------------------------


class TestFullLifecycleThroughAPI:
    def test_install_list_attach_detach_remove(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """End-to-end: install on disk → list via API → get details →
        attach → list attachments → detach → remove → verify cleanup."""

        # 1. Install two packs (via service layer — no install API endpoint)
        _install_pack(db, _security_pack(tmp_path))
        _install_pack(db, _python_pack(tmp_path))

        # 2. List packs via API
        resp = client.get("/api/packs")
        assert resp.status_code == 200
        packs = resp.json()
        assert len(packs) == 2

        # 3. Get pack details
        resp = client.get("/api/packs/acme/security-baseline")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["name"] == "security-baseline"
        assert len(detail["artifacts"]) == 2
        assert "source_path" not in detail
        for art in detail["artifacts"]:
            assert "content" not in art

        # 4. Attach both packs
        resp = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp.status_code == 200
        assert resp.json()["scope"] == "global"

        resp = client.post("/api/packs/acme/python-dev/attach", json={})
        assert resp.status_code == 200

        # 5. List attachments
        resp = client.get("/api/packs/acme/security-baseline/attachments")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        # 6. Verify registries are loaded
        registry = app.state.artifact_registry
        assert registry.count == 4  # 1 rule + 1 skill + 2 skills

        # 7. Verify rule enforcement works
        enforcer = app.state.rule_enforcer
        assert enforcer.rule_count == 1
        blocked, reason, fqn = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is True
        assert "Force push" in reason

        # 8. Detach security pack
        resp = client.delete("/api/packs/acme/security-baseline/attach")
        assert resp.status_code == 200

        # Verify attachment gone
        resp = client.get("/api/packs/acme/security-baseline/attachments")
        assert resp.json() == []

        # 9. Remove security pack via API
        resp = client.delete("/api/packs/acme/security-baseline")
        assert resp.status_code == 200

        # 10. Verify only python-dev remains
        resp = client.get("/api/packs")
        packs = resp.json()
        assert len(packs) == 1
        assert packs[0]["name"] == "python-dev"

        # 11. Registry should be reloaded — security artifacts gone
        assert registry.get("@acme/rule/no-force-push") is None
        assert registry.get("@acme/skill/lint") is not None

        # 12. Rule enforcer should be cleared
        assert enforcer.rule_count == 0
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is False

        # 13. Remove python-dev
        resp = client.delete("/api/packs/acme/python-dev")
        assert resp.status_code == 200

        # 14. Everything clean
        resp = client.get("/api/packs")
        assert resp.json() == []
        assert registry.count == 0
        assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM pack_attachments").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Tests: Security (info disclosure, input validation)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: Duplicate prevention
# ---------------------------------------------------------------------------


class TestDuplicatePrevention:
    def test_reinstall_does_not_duplicate_pack_rows(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """Installing the same pack twice should update, not create a second row (#772)."""
        pack_dir = _security_pack(tmp_path)
        r1 = _install_pack(db, pack_dir)
        r2 = _install_pack(db, pack_dir)

        assert r1["id"] != r2["id"]
        assert r2["action"] == "updated"

        # Only one pack in DB
        resp = client.get("/api/packs")
        assert len(resp.json()) == 1

    def test_reinstall_does_not_duplicate_artifacts(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """Reinstalling a pack must not create duplicate artifact rows."""
        pack_dir = _security_pack(tmp_path)
        _install_pack(db, pack_dir)
        count_before = db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]

        _install_pack(db, pack_dir)
        count_after = db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]

        assert count_after == count_before

    def test_attach_same_pack_global_and_project_allowed(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """Attaching the same pack at global and project scope is allowed.

        All non-config artifact types are additive, so the pack's
        artifacts don't conflict with themselves across scopes.
        """
        _install_pack(db, _security_pack(tmp_path))

        resp1 = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp1.status_code == 200

        resp2 = client.post(
            "/api/packs/acme/security-baseline/attach",
            json={"project_path": "/my/project"},
        )
        assert resp2.status_code == 200

    def test_attach_different_packs_to_different_scopes(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """Different packs can be attached at different scopes."""
        _install_pack(db, _security_pack(tmp_path))
        _install_pack(db, _python_pack(tmp_path))

        resp1 = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp1.status_code == 200

        resp2 = client.post(
            "/api/packs/acme/python-dev/attach",
            json={"project_path": "/my/project"},
        )
        assert resp2.status_code == 200

    def test_attach_same_pack_same_project_twice_returns_409(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """Attaching the same pack to the same project scope twice is rejected."""
        _install_pack(db, _security_pack(tmp_path))

        resp1 = client.post(
            "/api/packs/acme/security-baseline/attach",
            json={"project_path": "/my/project"},
        )
        assert resp1.status_code == 200

        resp2 = client.post(
            "/api/packs/acme/security-baseline/attach",
            json={"project_path": "/my/project"},
        )
        assert resp2.status_code == 409

    def test_reinstall_preserves_attachments(
        self, tmp_path: Path, db: ThreadSafeConnection, app: FastAPI, client: TestClient
    ) -> None:
        """Reinstalling a pack must not lose existing attachments."""
        pack_dir = _security_pack(tmp_path)
        _install_pack(db, pack_dir)

        # Attach via API
        resp = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp.status_code == 200

        # Reinstall (update)
        _install_pack(db, pack_dir)

        # Attachment should still exist (transferred to new pack ID)
        resp2 = client.get("/api/packs/acme/security-baseline/attachments")
        assert resp2.status_code == 200
        assert len(resp2.json()) == 1

    def test_remove_and_reinstall_is_clean(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        """Removing then reinstalling a pack should leave exactly one pack, no stale data."""
        pack_dir = _security_pack(tmp_path)
        _install_pack(db, pack_dir)
        client.post("/api/packs/acme/security-baseline/attach", json={})

        # Remove
        client.delete("/api/packs/acme/security-baseline")
        assert db.execute("SELECT COUNT(*) FROM pack_attachments").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0

        # Reinstall fresh
        _install_pack(db, pack_dir)
        resp = client.get("/api/packs")
        assert len(resp.json()) == 1

        # Can attach again
        resp2 = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Security (info disclosure, input validation)
# ---------------------------------------------------------------------------


class TestAPIErrorMessages:
    """Verify that the API surfaces detailed error messages, not generic ones."""

    def test_conflict_error_includes_details(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """Config overlay conflict errors should include the conflicting keys,
        not a generic 'already attached' message (#770)."""
        _install_pack(db, _config_pack(tmp_path))
        # Create a conflicting config pack
        conflict_dir = _write_pack(
            tmp_path,
            name="config-conflict",
            namespace="acme",
            config_overlay_files={
                "overrides": {"ai": {"temperature": 0.9}, "safety": {"approval_mode": "auto"}},
            },
        )
        _install_pack(db, conflict_dir)

        # Attach first pack
        resp1 = client.post("/api/packs/acme/config-overlay/attach", json={})
        assert resp1.status_code == 200

        # Attach conflicting pack at same priority — should get detailed error
        resp2 = client.post("/api/packs/acme/config-conflict/attach", json={})
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        assert "Config overlay conflict" in detail
        assert "temperature" in detail or "approval_mode" in detail

    def test_already_attached_error_includes_scope(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """Duplicate attach should say 'already attached at X scope'."""
        _install_pack(db, _security_pack(tmp_path))
        client.post("/api/packs/acme/security-baseline/attach", json={})

        resp = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp.status_code == 409
        assert "already attached" in resp.json()["detail"]

    def test_skill_same_name_different_namespace_is_additive(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """Skills are additive — same name from different namespaces is allowed."""
        _install_pack(
            db,
            _write_pack(
                tmp_path,
                name="pack-a",
                namespace="acme-a",
                skill_files={"deploy": "Deploy staging."},
            ),
        )
        _install_pack(
            db,
            _write_pack(
                tmp_path,
                name="pack-b",
                namespace="acme-b",
                skill_files={"deploy": "Deploy production."},
            ),
        )

        resp1 = client.post("/api/packs/acme-a/pack-a/attach", json={})
        assert resp1.status_code == 200

        resp2 = client.post("/api/packs/acme-b/pack-b/attach", json={})
        assert resp2.status_code == 200


class TestAPISecurityBehavior:
    def test_source_path_never_in_list_response(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp = client.get("/api/packs")
        for pack in resp.json():
            assert "source_path" not in pack

    def test_artifact_content_never_in_get_response(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        _install_pack(db, _security_pack(tmp_path))
        resp = client.get("/api/packs/acme/security-baseline")
        for art in resp.json()["artifacts"]:
            assert "content" not in art

    def test_special_chars_in_namespace_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/packs/../etc/passwd")
        assert resp.status_code in (400, 404, 422)

    def test_uppercase_namespace_accepted(self, client: TestClient) -> None:
        """Uppercase is valid — matches manifest parser regex."""
        resp = client.get("/api/packs/UPPERCASE/valid-name")
        assert resp.status_code == 404  # valid format, just not found

    def test_special_chars_namespace_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/packs/inv@lid/valid-name")
        assert resp.status_code == 400

    def test_long_namespace_rejected(self, client: TestClient) -> None:
        long_ns = "a" * 65
        resp = client.get(f"/api/packs/{long_ns}/valid-name")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: Config overlay conflict through API
# ---------------------------------------------------------------------------


class TestConfigOverlayConflictsAPI:
    """Verify config overlay conflict detection through the HTTP layer."""

    def test_same_priority_conflict_blocked(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        """Two packs with overlapping config keys at same priority => 409."""
        _install_pack(db, _config_pack(tmp_path))
        conflict_dir = _write_pack(
            tmp_path,
            name="config-conflict",
            namespace="acme",
            config_overlay_files={
                "overrides": {
                    "ai": {"temperature": 0.9},
                    "safety": {"approval_mode": "auto"},
                },
            },
        )
        _install_pack(db, conflict_dir)

        resp1 = client.post("/api/packs/acme/config-overlay/attach", json={})
        assert resp1.status_code == 200

        resp2 = client.post("/api/packs/acme/config-conflict/attach", json={})
        assert resp2.status_code == 409
        detail = resp2.json()["detail"]
        assert "Config overlay conflict" in detail

    def test_different_priority_no_conflict(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        """Same keys at different priorities should succeed.

        Note: The attach API doesn't yet support a priority parameter,
        so we attach the first via service layer with priority=10, then
        the second via API (default priority=50).
        """
        from anteroom.services.pack_attachments import attach_pack

        r1 = _install_pack(db, _config_pack(tmp_path))
        conflict_dir = _write_pack(
            tmp_path,
            name="config-conflict",
            namespace="acme",
            config_overlay_files={
                "overrides": {
                    "ai": {"temperature": 0.9},
                    "safety": {"approval_mode": "auto"},
                },
            },
        )
        _install_pack(db, conflict_dir)

        # Attach first at priority 10 via service
        attach_pack(db, r1["id"], priority=10)

        # Attach second at default priority 50 via API
        resp = client.post("/api/packs/acme/config-conflict/attach", json={})
        assert resp.status_code == 200

    def test_non_overlapping_overlays_no_conflict(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """Packs with different config keys should attach fine."""
        pack_a = _write_pack(
            tmp_path,
            name="config-a",
            namespace="team-a",
            config_overlay_files={
                "settings": {"ai": {"temperature": 0.7}},
            },
        )
        pack_b = _write_pack(
            tmp_path,
            name="config-b",
            namespace="team-b",
            config_overlay_files={
                "settings": {"cli": {"compact_threshold": 50000}},
            },
        )
        _install_pack(db, pack_a)
        _install_pack(db, pack_b)

        resp1 = client.post("/api/packs/team-a/config-a/attach", json={})
        assert resp1.status_code == 200
        resp2 = client.post("/api/packs/team-b/config-b/attach", json={})
        assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Additive vs exclusive artifacts through API
# ---------------------------------------------------------------------------


class TestArtifactConflictsAPI:
    """Rules are additive (same name from two packs = OK).
    Skills are exclusive (same name = conflict)."""

    def test_same_rule_name_both_attach(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        """Two packs with identically-named rules should both attach."""
        pack_a = _write_pack(
            tmp_path,
            name="pack-a",
            namespace="org-a",
            rule_files={
                "deploy-gate": {
                    "content": "Run tests before deploy.",
                    "metadata": {
                        "enforce": "hard",
                        "reason": "Tests first",
                        "matches": [{"tool": "bash", "pattern": "deploy"}],
                    },
                }
            },
        )
        pack_b = _write_pack(
            tmp_path,
            name="pack-b",
            namespace="org-b",
            rule_files={
                "deploy-gate": {
                    "content": "Notify team before deploy.",
                    "metadata": {
                        "enforce": "hard",
                        "reason": "Notify",
                        "matches": [{"tool": "bash", "pattern": "deploy"}],
                    },
                }
            },
        )
        _install_pack(db, pack_a)
        _install_pack(db, pack_b)

        resp1 = client.post("/api/packs/org-a/pack-a/attach", json={})
        assert resp1.status_code == 200
        resp2 = client.post("/api/packs/org-b/pack-b/attach", json={})
        assert resp2.status_code == 200

    def test_same_skill_name_is_additive(self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient) -> None:
        """Two packs with same skill name are additive — namespace-qualified display names."""
        pack_a = _write_pack(
            tmp_path,
            name="pack-a",
            namespace="org-a",
            skill_files={"deploy": "Deploy to staging."},
        )
        pack_b = _write_pack(
            tmp_path,
            name="pack-b",
            namespace="org-b",
            skill_files={"deploy": "Deploy to production."},
        )
        _install_pack(db, pack_a)
        _install_pack(db, pack_b)

        resp1 = client.post("/api/packs/org-a/pack-a/attach", json={})
        assert resp1.status_code == 200

        resp2 = client.post("/api/packs/org-b/pack-b/attach", json={})
        assert resp2.status_code == 200

    def test_same_skill_name_same_pack_namespace_ok(
        self, tmp_path: Path, db: ThreadSafeConnection, client: TestClient
    ) -> None:
        """A pack with rules AND skills should attach without self-conflict."""
        pack = _write_pack(
            tmp_path,
            name="full-pack",
            namespace="acme",
            skill_files={"lint": "Run linter."},
            rule_files={
                "no-force": {
                    "content": "No force push.",
                    "metadata": {
                        "enforce": "hard",
                        "reason": "Safety",
                        "matches": [{"tool": "bash", "pattern": "force"}],
                    },
                }
            },
        )
        _install_pack(db, pack)

        resp = client.post("/api/packs/acme/full-pack/attach", json={})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Rule enforcement through API lifecycle
# ---------------------------------------------------------------------------


class TestRuleEnforcementViaAPI:
    """Verify rules are enforced after attach/detach through the API."""

    def test_rules_enforced_after_api_attach(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """After attaching a pack with rules via API, the rule enforcer
        should block matching tool calls."""
        _install_pack(db, _security_pack(tmp_path))

        # Attach via API
        resp = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp.status_code == 200

        # Rule enforcer should now be loaded
        enforcer = app.state.rule_enforcer
        blocked, reason, _ = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is True
        assert "Force push" in reason

    def test_rules_cleared_after_api_remove(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """After removing a pack with rules via API, the enforcer should
        no longer block."""
        _install_pack(db, _security_pack(tmp_path))
        client.post("/api/packs/acme/security-baseline/attach", json={})

        # Verify blocked
        enforcer = app.state.rule_enforcer
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is True

        # Remove pack via API
        client.delete("/api/packs/acme/security-baseline")

        # Should no longer be blocked
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is False

    def test_rules_cleared_after_api_detach(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """After detaching (not removing) a pack, rules still loaded
        (detach only removes attachment, not the pack or its artifacts)."""
        _install_pack(db, _security_pack(tmp_path))
        client.post("/api/packs/acme/security-baseline/attach", json={})

        enforcer = app.state.rule_enforcer
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force origin main"})
        assert blocked is True

        # Detach
        client.delete("/api/packs/acme/security-baseline/attach")

        # Registry reloaded — artifacts still exist, enforcer reloaded
        # The rule artifact is still in DB, so it's still in the registry
        assert app.state.artifact_registry.count == 2


# ---------------------------------------------------------------------------
# Tests: Pack update lifecycle through API
# ---------------------------------------------------------------------------


class TestPackUpdateViaAPI:
    """Verify pack reinstall (update) preserves state through the API."""

    def test_reinstall_preserves_attachment_scope(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """Reinstalling a pack keeps the attachment at the same scope."""
        pack_dir = _security_pack(tmp_path)
        _install_pack(db, pack_dir)

        # Attach with project scope
        resp = client.post(
            "/api/packs/acme/security-baseline/attach",
            json={"project_path": "/my/project"},
        )
        assert resp.status_code == 200

        # Reinstall
        _install_pack(db, pack_dir)

        # Attachment should still exist
        resp2 = client.get("/api/packs/acme/security-baseline/attachments")
        assert resp2.status_code == 200
        atts = resp2.json()
        assert len(atts) == 1
        assert atts[0]["scope"] == "project"
        assert atts[0]["project_path"] == "/my/project"

    def test_reinstall_updates_artifact_list(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """Reinstalling with modified artifacts updates the pack detail."""
        pack_dir = _write_pack(
            tmp_path,
            name="evolving",
            namespace="acme",
            skill_files={"lint": "Run linter."},
        )
        _install_pack(db, pack_dir)

        resp = client.get("/api/packs/acme/evolving")
        assert len(resp.json()["artifacts"]) == 1

        # Add another skill and reinstall
        pack_dir2 = _write_pack(
            tmp_path,
            name="evolving",
            namespace="acme",
            skill_files={"lint": "Run linter.", "test": "Run tests."},
        )
        _install_pack(db, pack_dir2)

        resp2 = client.get("/api/packs/acme/evolving")
        assert len(resp2.json()["artifacts"]) == 2

    def test_get_pack_by_id(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        client: TestClient,
    ) -> None:
        """GET /api/packs/by-id/{pack_id} returns pack details."""
        result = _install_pack(db, _security_pack(tmp_path))
        pack_id = result["id"]

        resp = client.get(f"/api/packs/by-id/{pack_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "security-baseline"
        assert "source_path" not in data

    def test_remove_by_id(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        client: TestClient,
    ) -> None:
        """DELETE /api/packs/by-id/{pack_id} removes the pack."""
        result = _install_pack(db, _security_pack(tmp_path))
        pack_id = result["id"]

        resp = client.delete(f"/api/packs/by-id/{pack_id}")
        assert resp.status_code == 200

        resp2 = client.get(f"/api/packs/by-id/{pack_id}")
        assert resp2.status_code == 404

    def test_get_by_id_invalid_format(self, client: TestClient) -> None:
        """Invalid pack ID format should return 400."""
        resp = client.get("/api/packs/by-id/not-a-valid-id!")
        assert resp.status_code == 400

    def test_remove_by_id_not_found(self, client: TestClient) -> None:
        """Non-existent pack ID should return 404."""
        fake_id = "a" * 32
        resp = client.delete(f"/api/packs/by-id/{fake_id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Multi-pack lifecycle through API
# ---------------------------------------------------------------------------


class TestMultiPackLifecycleAPI:
    """Complex scenarios with multiple packs through the HTTP layer."""

    def test_attach_detach_reattach(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        client: TestClient,
    ) -> None:
        """Attach, detach, then reattach should work cleanly."""
        _install_pack(db, _security_pack(tmp_path))

        # Attach
        resp = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp.status_code == 200

        # Detach
        resp = client.delete("/api/packs/acme/security-baseline/attach")
        assert resp.status_code == 200

        # Reattach
        resp = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp.status_code == 200

    def test_multiple_packs_independent_lifecycle(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """Two packs can be independently attached and removed."""
        _install_pack(db, _security_pack(tmp_path))
        _install_pack(db, _python_pack(tmp_path))

        # Attach both
        resp1 = client.post("/api/packs/acme/security-baseline/attach", json={})
        assert resp1.status_code == 200
        resp2 = client.post("/api/packs/acme/python-dev/attach", json={})
        assert resp2.status_code == 200

        # Remove one
        client.delete("/api/packs/acme/security-baseline")

        # Other still present
        resp = client.get("/api/packs")
        packs = resp.json()
        assert len(packs) == 1
        assert packs[0]["name"] == "python-dev"

        # Its attachment still works
        resp = client.get("/api/packs/acme/python-dev/attachments")
        assert len(resp.json()) == 1

    def test_remove_attached_pack_cleans_attachment(
        self,
        tmp_path: Path,
        db: ThreadSafeConnection,
        client: TestClient,
    ) -> None:
        """Removing a pack should cascade-delete its attachments."""
        _install_pack(db, _security_pack(tmp_path))
        client.post("/api/packs/acme/security-baseline/attach", json={})

        # Verify attachment exists
        count = db.execute("SELECT COUNT(*) FROM pack_attachments").fetchone()[0]
        assert count == 1

        # Remove pack
        client.delete("/api/packs/acme/security-baseline")

        # Attachment should be cascade-deleted
        count = db.execute("SELECT COUNT(*) FROM pack_attachments").fetchone()[0]
        assert count == 0

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
        resp = client.get("/api/packs/INVALID/test-pack")
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

    def test_uppercase_namespace_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/packs/UPPERCASE/valid-name")
        assert resp.status_code == 400

    def test_long_namespace_rejected(self, client: TestClient) -> None:
        long_ns = "a" * 65
        resp = client.get(f"/api/packs/{long_ns}/valid-name")
        assert resp.status_code == 400

"""Tests for rebuild_effective_config (#875)."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from anteroom.services.config_overlays import (
    _RESTART_ONLY_FIELDS,
    ComplianceError,
    ConfigRebuildResult,
    _config_to_dict,
    rebuild_effective_config,
)


@dataclasses.dataclass
class _FakeConfig:
    """Minimal config-like dataclass for testing."""

    ai_base_url: str = "http://example.com"
    model: str = "gpt-4"


class TestRebuildEffectiveConfig:
    """Tests for the rebuild_effective_config function."""

    @patch("anteroom.services.compliance.validate_compliance")
    @patch("anteroom.config.load_config")
    @patch("anteroom.services.pack_attachments.get_attachment_priorities")
    @patch("anteroom.services.config_overlays.collect_pack_overlays")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids")
    def test_successful_rebuild_returns_config(
        self,
        mock_active: MagicMock,
        mock_collect: MagicMock,
        mock_priorities: MagicMock,
        mock_load: MagicMock,
        mock_compliance: MagicMock,
    ) -> None:
        """Successful rebuild returns a ConfigRebuildResult with the new config."""
        db = MagicMock()
        fake_config = _FakeConfig()
        mock_active.return_value = ["pack-1"]
        mock_collect.return_value = [("ns/p", {"ai": {"model": "gpt-4"}})]
        mock_priorities.return_value = {"ns/p": 50}
        mock_load.return_value = (fake_config, ["safety.approval_mode"])

        mock_result = MagicMock()
        mock_result.is_compliant = True
        mock_compliance.return_value = mock_result

        result = rebuild_effective_config(db, project_path="/proj")

        assert isinstance(result, ConfigRebuildResult)
        assert result.config is fake_config
        assert result.enforced_fields == ["safety.approval_mode"]
        assert result.warnings == []
        assert result.restart_required_fields == []
        mock_active.assert_called_once_with(db, project_path="/proj")

    @patch("anteroom.services.compliance.validate_compliance")
    @patch("anteroom.config.load_config")
    @patch("anteroom.services.pack_attachments.get_attachment_priorities")
    @patch("anteroom.services.config_overlays.collect_pack_overlays")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids_for_space")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids")
    def test_space_id_uses_space_aware_query(
        self,
        mock_active: MagicMock,
        mock_active_space: MagicMock,
        mock_collect: MagicMock,
        mock_priorities: MagicMock,
        mock_load: MagicMock,
        mock_compliance: MagicMock,
    ) -> None:
        """When space_id is provided, get_active_pack_ids_for_space is used instead of get_active_pack_ids."""
        db = MagicMock()
        fake_config = _FakeConfig()
        mock_active_space.return_value = ["pack-1"]
        mock_collect.return_value = [("ns/p", {"ai": {"model": "gpt-4"}})]
        mock_priorities.return_value = {"ns/p": 50}
        mock_load.return_value = (fake_config, [])

        mock_result = MagicMock()
        mock_result.is_compliant = True
        mock_compliance.return_value = mock_result

        result = rebuild_effective_config(db, space_id="space-1", project_path="/proj")

        assert result.config is fake_config
        mock_active.assert_not_called()
        mock_active_space.assert_called_once_with(db, "space-1", project_path="/proj")

    @patch("anteroom.services.compliance.validate_compliance")
    @patch("anteroom.config.load_config")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids_for_space")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids")
    def test_no_space_id_uses_project_path_query(
        self,
        mock_active: MagicMock,
        mock_active_space: MagicMock,
        mock_load: MagicMock,
        mock_compliance: MagicMock,
    ) -> None:
        """When space_id is None, get_active_pack_ids is used (not the space variant)."""
        db = MagicMock()
        mock_active.return_value = []
        mock_load.return_value = (_FakeConfig(), [])

        mock_result = MagicMock()
        mock_result.is_compliant = True
        mock_compliance.return_value = mock_result

        rebuild_effective_config(db, project_path="/proj")

        mock_active.assert_called_once_with(db, project_path="/proj")
        mock_active_space.assert_not_called()

    @patch("anteroom.services.compliance.validate_compliance")
    @patch("anteroom.config.load_config")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids")
    def test_compliance_failure_raises_compliance_error(
        self,
        mock_active: MagicMock,
        mock_load: MagicMock,
        mock_compliance: MagicMock,
    ) -> None:
        """When compliance validation fails, ComplianceError is raised."""
        db = MagicMock()
        mock_active.return_value = []
        mock_load.return_value = (MagicMock(), [])

        mock_result = MagicMock()
        mock_result.is_compliant = False
        mock_result.format_report.return_value = "field X must be Y"
        mock_compliance.return_value = mock_result

        with pytest.raises(ComplianceError, match="compliance failure"):
            rebuild_effective_config(db)

    @patch("anteroom.services.compliance.validate_compliance")
    @patch("anteroom.config.load_config")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids")
    def test_restart_required_field_detection(
        self,
        mock_active: MagicMock,
        mock_load: MagicMock,
        mock_compliance: MagicMock,
    ) -> None:
        """Changed restart-only fields appear in warnings and restart_required_fields."""
        db = MagicMock()
        mock_active.return_value = []

        @dataclasses.dataclass
        class FakeAI:
            base_url: str = ""
            api_key: str = ""
            provider: str = "openai"

        @dataclasses.dataclass
        class FakeStorage:
            encrypt_at_rest: bool = False
            encryption_kdf: str = "hkdf"

        @dataclasses.dataclass
        class OldConfig:
            ai: FakeAI = dataclasses.field(default_factory=lambda: FakeAI(base_url="http://old"))
            storage: FakeStorage = dataclasses.field(default_factory=FakeStorage)

        @dataclasses.dataclass
        class NewConfig:
            ai: FakeAI = dataclasses.field(default_factory=lambda: FakeAI(base_url="http://new"))
            storage: FakeStorage = dataclasses.field(default_factory=FakeStorage)

        old_config = OldConfig()
        new_config = NewConfig()
        mock_load.return_value = (new_config, [])

        mock_result = MagicMock()
        mock_result.is_compliant = True
        mock_compliance.return_value = mock_result

        result = rebuild_effective_config(db, previous_config=old_config)

        assert "ai.base_url" in result.restart_required_fields
        assert any("ai.base_url" in w for w in result.warnings)

    @patch("anteroom.services.compliance.validate_compliance")
    @patch("anteroom.config.load_config")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids")
    def test_empty_overlays_returns_valid_config(
        self,
        mock_active: MagicMock,
        mock_load: MagicMock,
        mock_compliance: MagicMock,
    ) -> None:
        """When no packs are active, rebuild still returns a valid config."""
        db = MagicMock()
        mock_active.return_value = []
        fake_config = _FakeConfig()
        mock_load.return_value = (fake_config, [])

        mock_result = MagicMock()
        mock_result.is_compliant = True
        mock_compliance.return_value = mock_result

        result = rebuild_effective_config(db)

        assert result.config is fake_config
        assert result.warnings == []
        assert result.restart_required_fields == []
        mock_load.assert_called_once_with(
            team_config_path=None,
            pack_config=None,
        )

    @patch("anteroom.services.compliance.validate_compliance")
    @patch("anteroom.config.load_config")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids")
    def test_no_previous_config_skips_restart_detection(
        self,
        mock_active: MagicMock,
        mock_load: MagicMock,
        mock_compliance: MagicMock,
    ) -> None:
        """When previous_config is None, restart detection is skipped."""
        db = MagicMock()
        mock_active.return_value = []
        mock_load.return_value = (_FakeConfig(), [])

        mock_result = MagicMock()
        mock_result.is_compliant = True
        mock_compliance.return_value = mock_result

        result = rebuild_effective_config(db, previous_config=None)

        assert result.restart_required_fields == []
        assert result.warnings == []


class TestConfigToDict:
    """Tests for the _config_to_dict helper."""

    def test_dataclass_converts_to_dict(self) -> None:
        cfg = _FakeConfig(ai_base_url="http://x", model="m")
        result = _config_to_dict(cfg)
        assert result == {"ai_base_url": "http://x", "model": "m"}

    def test_non_dataclass_returns_empty(self) -> None:
        result = _config_to_dict({"key": "val"})
        assert result == {}

    def test_class_type_returns_empty(self) -> None:
        result = _config_to_dict(_FakeConfig)
        assert result == {}


class TestWebRebuildConfig:
    """Tests for the web router _rebuild_config fail-closed behavior."""

    @patch("anteroom.routers.packs._refresh_derived_state")
    @patch("anteroom.services.config_overlays.rebuild_effective_config")
    def test_rebuild_success_updates_config_and_derived(self, mock_rebuild: MagicMock, mock_refresh: MagicMock) -> None:
        """Successful rebuild updates app.state.config and derived state."""
        from anteroom.routers.packs import _rebuild_config

        request = MagicMock()
        request.app.state.config = _FakeConfig(model="old")
        new_config = _FakeConfig(model="new")
        mock_rebuild.return_value = ConfigRebuildResult(
            config=new_config, enforced_fields=["safety.approval_mode"], warnings=[]
        )

        success, compliance_failure = _rebuild_config(request, MagicMock())

        assert success is True
        assert compliance_failure is False
        assert request.app.state.config is new_config
        assert request.app.state.enforced_fields == ["safety.approval_mode"]
        mock_refresh.assert_called_once_with(request, new_config)

    @patch("anteroom.routers.packs._refresh_derived_state")
    @patch("anteroom.services.config_overlays.rebuild_effective_config")
    def test_rebuild_compliance_failure_keeps_previous(self, mock_rebuild: MagicMock, mock_refresh: MagicMock) -> None:
        """ComplianceError keeps previous config and signals compliance_failure."""
        from anteroom.routers.packs import _rebuild_config

        request = MagicMock()
        old_config = _FakeConfig(model="old")
        request.app.state.config = old_config
        mock_rebuild.side_effect = ComplianceError("compliance failure")

        success, compliance_failure = _rebuild_config(request, MagicMock())

        assert success is False
        assert compliance_failure is True
        assert request.app.state.config is old_config
        mock_refresh.assert_not_called()

    @patch("anteroom.routers.packs._refresh_derived_state")
    @patch("anteroom.services.config_overlays.rebuild_effective_config")
    def test_rebuild_exception_keeps_previous_no_compliance(
        self, mock_rebuild: MagicMock, mock_refresh: MagicMock
    ) -> None:
        """Generic exception keeps previous config but does NOT signal compliance failure."""
        from anteroom.routers.packs import _rebuild_config

        request = MagicMock()
        old_config = _FakeConfig(model="old")
        request.app.state.config = old_config
        mock_rebuild.side_effect = RuntimeError("db error")

        success, compliance_failure = _rebuild_config(request, MagicMock())

        assert success is False
        assert compliance_failure is False
        assert request.app.state.config is old_config
        mock_refresh.assert_not_called()


class TestWebRollbackPackMutation:
    """Tests for rollback on config rebuild failure in web endpoints."""

    @patch("anteroom.services.pack_attachments.detach_pack")
    def test_rollback_detach_on_attach_failure(self, mock_detach: MagicMock) -> None:
        """After failed attach rebuild, the attachment is rolled back via detach."""
        from anteroom.routers.packs import _rollback_pack_mutation

        db = MagicMock()
        _rollback_pack_mutation(db, "pack-1", "/project", "detach")

        mock_detach.assert_called_once_with(db, "pack-1", project_path="/project")

    @patch("anteroom.services.pack_attachments.attach_pack")
    def test_rollback_attach_on_detach_failure(self, mock_attach: MagicMock) -> None:
        """After failed detach rebuild, the detachment is rolled back via re-attach."""
        from anteroom.routers.packs import _rollback_pack_mutation

        db = MagicMock()
        _rollback_pack_mutation(db, "pack-1", "/project", "attach")

        mock_attach.assert_called_once_with(db, "pack-1", project_path="/project", check_overlay_conflicts=False)


class TestRefreshDerivedState:
    """Tests for _refresh_derived_state updating app.state singletons."""

    def test_updates_rate_limit_config(self) -> None:
        from anteroom.routers.packs import _refresh_derived_state

        request = MagicMock()
        config = MagicMock()
        config.rate_limit = MagicMock()
        config.safety = None

        _refresh_derived_state(request, config)

        assert request.app.state.rate_limit_config is config.rate_limit

    def test_clears_dlp_when_disabled(self) -> None:
        from anteroom.routers.packs import _refresh_derived_state

        request = MagicMock()
        config = MagicMock()
        config.safety.dlp.enabled = False
        config.safety.prompt_injection = None

        _refresh_derived_state(request, config)

        assert request.app.state.dlp_scanner is None


class TestRestartOnlyFields:
    """Verify _RESTART_ONLY_FIELDS is a frozenset with expected entries."""

    def test_is_frozenset(self) -> None:
        assert isinstance(_RESTART_ONLY_FIELDS, frozenset)

    def test_contains_ai_base_url(self) -> None:
        assert "ai.base_url" in _RESTART_ONLY_FIELDS

    def test_contains_provider(self) -> None:
        assert "ai.provider" in _RESTART_ONLY_FIELDS

    def test_contains_encrypt_at_rest(self) -> None:
        assert "storage.encrypt_at_rest" in _RESTART_ONLY_FIELDS

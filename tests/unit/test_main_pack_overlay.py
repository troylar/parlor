"""Tests for _collect_pack_overlay with project_path support (#875)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestCollectPackOverlayProjectPath:
    """Verify _collect_pack_overlay passes project_path to get_active_pack_ids."""

    @patch("anteroom.services.pack_attachments.get_active_pack_ids")
    @patch("anteroom.db.get_db")
    @patch("anteroom.config._resolve_data_dir")
    def test_no_project_path_calls_global_only(
        self,
        mock_resolve: MagicMock,
        mock_get_db: MagicMock,
        mock_active_ids: MagicMock,
        tmp_path: MagicMock,
    ) -> None:
        """When project_path is None, get_active_pack_ids receives project_path=None."""
        from anteroom.__main__ import _collect_pack_overlay

        mock_resolve.return_value = tmp_path
        (tmp_path / "chat.db").touch()
        mock_active_ids.return_value = []

        _collect_pack_overlay()

        mock_active_ids.assert_called_once_with(mock_get_db.return_value, project_path=None)

    @patch("anteroom.services.pack_attachments.get_attachment_priorities")
    @patch("anteroom.services.config_overlays.merge_pack_overlays")
    @patch("anteroom.services.config_overlays.collect_pack_overlays")
    @patch("anteroom.services.pack_attachments.get_active_pack_ids")
    @patch("anteroom.db.get_db")
    @patch("anteroom.config._resolve_data_dir")
    def test_with_project_path_includes_project_scoped(
        self,
        mock_resolve: MagicMock,
        mock_get_db: MagicMock,
        mock_active_ids: MagicMock,
        mock_collect: MagicMock,
        mock_merge: MagicMock,
        mock_priorities: MagicMock,
        tmp_path: MagicMock,
    ) -> None:
        """When project_path is set, get_active_pack_ids receives it."""
        from anteroom.__main__ import _collect_pack_overlay

        mock_resolve.return_value = tmp_path
        (tmp_path / "chat.db").touch()
        mock_active_ids.return_value = ["pack-1"]
        mock_collect.return_value = [("ns/name", {"ai": {"model": "gpt-4"}})]
        mock_priorities.return_value = {"ns/name": 50}
        mock_merge.return_value = {"ai": {"model": "gpt-4"}}

        result = _collect_pack_overlay(project_path="/some/project")

        mock_active_ids.assert_called_once_with(mock_get_db.return_value, project_path="/some/project")
        assert result == {"ai": {"model": "gpt-4"}}

    @patch("anteroom.config._resolve_data_dir")
    def test_no_db_returns_none(
        self,
        mock_resolve: MagicMock,
        tmp_path: MagicMock,
    ) -> None:
        """When DB file does not exist, returns None without querying."""
        from anteroom.__main__ import _collect_pack_overlay

        mock_resolve.return_value = tmp_path
        # Do NOT create chat.db

        result = _collect_pack_overlay(project_path="/some/project")

        assert result is None


class TestLoadConfigOrExitProjectPath:
    """Verify _load_config_or_exit forwards project_path."""

    @patch("anteroom.__main__._collect_pack_overlay")
    @patch("anteroom.__main__._get_config_path")
    @patch("anteroom.__main__.load_config")
    @patch("anteroom.services.compliance.validate_compliance")
    def test_project_path_forwarded_to_collect(
        self,
        mock_compliance: MagicMock,
        mock_load: MagicMock,
        mock_config_path: MagicMock,
        mock_collect: MagicMock,
        tmp_path: MagicMock,
    ) -> None:
        """project_path kwarg is forwarded to _collect_pack_overlay."""
        from anteroom.__main__ import _load_config_or_exit

        config_file = tmp_path / "config.yaml"
        config_file.touch()
        mock_config_path.return_value = config_file
        mock_collect.return_value = None

        mock_config = MagicMock()
        mock_load.return_value = (mock_config, [])

        mock_result = MagicMock()
        mock_result.is_compliant = True
        mock_compliance.return_value = mock_result

        _load_config_or_exit(project_path="/my/project")

        mock_collect.assert_called_once_with(project_path="/my/project")

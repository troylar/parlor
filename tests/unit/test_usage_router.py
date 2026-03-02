"""Unit tests for the /usage router endpoint (issue #689).

Tests cover:
- No query params (all four periods returned)
- period= filter (day, week, month, all)
- conversation_id= filter
- Combined period + conversation_id
- Cost calculation with known and unknown models
- None/missing token values handled as 0
- Invalid period value (422 from FastAPI query validator)
- Invalid conversation_id UUID pattern (422)
- Empty stats list (all zeros)
- Multiple models in a single period
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.config import CliConfig, UsageConfig
from anteroom.routers.usage import router

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

_VALID_CONV_ID = "12345678-1234-1234-1234-123456789abc"


def _make_app(usage_cfg: UsageConfig | None = None) -> FastAPI:
    """Build a minimal FastAPI app with the usage router and mocked state."""
    app = FastAPI()
    app.include_router(router, prefix="/api")

    mock_db = MagicMock()
    app.state.db = mock_db

    cfg = usage_cfg or UsageConfig()
    mock_config = MagicMock()
    mock_config.cli = CliConfig(usage=cfg)
    app.state.config = mock_config

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stat(
    model: str = "gpt-4o",
    prompt_tokens: int = 100,
    completion_tokens: int = 200,
    total_tokens: int = 300,
    message_count: int = 2,
) -> dict:
    return {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "message_count": message_count,
    }


# ---------------------------------------------------------------------------
# No query parameters — all four periods returned
# ---------------------------------------------------------------------------


class TestGetUsageNoPeriod:
    def test_returns_all_four_periods(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            resp = client.get("/api/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"Today", "This week", "This month", "All time"}

    def test_all_zeros_when_no_stats(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            resp = client.get("/api/usage")
        data = resp.json()
        for label in ("Today", "This week", "This month", "All time"):
            assert data[label]["prompt_tokens"] == 0
            assert data[label]["completion_tokens"] == 0
            assert data[label]["total_tokens"] == 0
            assert data[label]["message_count"] == 0
            assert data[label]["estimated_cost"] == 0.0
            assert data[label]["by_model"] == []

    def test_storage_called_four_times(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            client.get("/api/usage")
        assert mock_storage.get_usage_stats.call_count == 4


# ---------------------------------------------------------------------------
# period= query parameter
# ---------------------------------------------------------------------------


class TestGetUsageWithPeriod:
    @pytest.mark.parametrize(
        "period,label",
        [
            ("day", "Today"),
            ("week", "This week"),
            ("month", "This month"),
            ("all", "All time"),
        ],
    )
    def test_single_period_returns_one_key(self, period: str, label: str) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            resp = client.get(f"/api/usage?period={period}")
        assert resp.status_code == 200
        data = resp.json()
        assert list(data.keys()) == [label]

    def test_single_period_storage_called_once(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            client.get("/api/usage?period=day")
        assert mock_storage.get_usage_stats.call_count == 1

    def test_period_all_passes_none_since(self) -> None:
        """'all' period should pass since=None to storage."""
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            client.get("/api/usage?period=all")
        call_kwargs = mock_storage.get_usage_stats.call_args
        assert call_kwargs.kwargs.get("since") is None

    def test_period_day_passes_since_isoformat(self) -> None:
        """'day' period should pass a non-None since string."""
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            client.get("/api/usage?period=day")
        call_kwargs = mock_storage.get_usage_stats.call_args
        since = call_kwargs.kwargs.get("since")
        assert since is not None
        # Should be a valid ISO timestamp string
        assert "T" in since

    def test_invalid_period_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/usage?period=yesterday")
        assert resp.status_code == 422

    def test_invalid_period_empty_string_returns_422(self) -> None:
        # An empty period= value doesn't match the pattern
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/usage?period=")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# conversation_id= query parameter
# ---------------------------------------------------------------------------


class TestGetUsageWithConversationId:
    def test_valid_conversation_id_passed_to_storage(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            client.get(f"/api/usage?conversation_id={_VALID_CONV_ID}")
        call_kwargs = mock_storage.get_usage_stats.call_args
        assert call_kwargs.kwargs.get("conversation_id") == _VALID_CONV_ID

    def test_invalid_conversation_id_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/usage?conversation_id=not-a-uuid")
        assert resp.status_code == 422

    def test_conversation_id_without_period_calls_storage_four_times(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            client.get(f"/api/usage?conversation_id={_VALID_CONV_ID}")
        assert mock_storage.get_usage_stats.call_count == 4


# ---------------------------------------------------------------------------
# Combined period + conversation_id
# ---------------------------------------------------------------------------


class TestGetUsageCombined:
    def test_period_and_conv_id_both_forwarded(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            client.get(f"/api/usage?period=week&conversation_id={_VALID_CONV_ID}")
        assert mock_storage.get_usage_stats.call_count == 1
        call_kwargs = mock_storage.get_usage_stats.call_args
        assert call_kwargs.kwargs.get("conversation_id") == _VALID_CONV_ID
        assert call_kwargs.kwargs.get("since") is not None


# ---------------------------------------------------------------------------
# Token aggregation
# ---------------------------------------------------------------------------


class TestTokenAggregation:
    def test_single_model_aggregation(self) -> None:
        app = _make_app()
        stats = [_stat(model="gpt-4o", prompt_tokens=100, completion_tokens=200, total_tokens=300, message_count=5)]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        data = resp.json()["All time"]
        assert data["prompt_tokens"] == 100
        assert data["completion_tokens"] == 200
        assert data["total_tokens"] == 300
        assert data["message_count"] == 5

    def test_multiple_models_aggregated(self) -> None:
        app = _make_app()
        stats = [
            _stat(model="gpt-4o", prompt_tokens=100, completion_tokens=200, total_tokens=300, message_count=2),
            _stat(model="gpt-4o-mini", prompt_tokens=50, completion_tokens=75, total_tokens=125, message_count=1),
        ]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        data = resp.json()["All time"]
        assert data["prompt_tokens"] == 150
        assert data["completion_tokens"] == 275
        assert data["total_tokens"] == 425
        assert data["message_count"] == 3

    def test_by_model_list_populated(self) -> None:
        app = _make_app()
        stats = [
            _stat(model="gpt-4o", prompt_tokens=100, completion_tokens=200, total_tokens=300, message_count=2),
            _stat(model="gpt-4o-mini", prompt_tokens=50, completion_tokens=75, total_tokens=125, message_count=1),
        ]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        by_model = resp.json()["All time"]["by_model"]
        assert len(by_model) == 2
        models = {m["model"] for m in by_model}
        assert models == {"gpt-4o", "gpt-4o-mini"}

    def test_none_token_values_treated_as_zero(self) -> None:
        """Stats with None token values should be summed as 0."""
        app = _make_app()
        stats = [
            {
                "model": "gpt-4o",
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "message_count": None,
            }
        ]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        data = resp.json()["All time"]
        assert data["prompt_tokens"] == 0
        assert data["completion_tokens"] == 0
        assert data["total_tokens"] == 0
        assert data["message_count"] == 0

    def test_missing_model_key_falls_back_to_unknown(self) -> None:
        """Stats dicts without a 'model' key should appear as 'unknown' in by_model."""
        app = _make_app()
        stats = [{"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "message_count": 1}]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        by_model = resp.json()["All time"]["by_model"]
        assert by_model[0]["model"] == "unknown"


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


class TestCostCalculation:
    def test_known_model_cost_calculated(self) -> None:
        """gpt-4o: input=$2.50/M, output=$10.00/M."""
        app = _make_app()
        # 1M prompt + 1M completion
        stats = [_stat(model="gpt-4o", prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        cost = resp.json()["All time"]["estimated_cost"]
        # 1M * $2.50 + 1M * $10.00 = $12.50
        assert cost == pytest.approx(12.50, abs=1e-4)

    def test_unknown_model_zero_cost(self) -> None:
        app = _make_app()
        stats = [_stat(model="some-unknown-model-xyz", prompt_tokens=1_000_000, completion_tokens=1_000_000)]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        cost = resp.json()["All time"]["estimated_cost"]
        assert cost == 0.0

    def test_cost_rounded_to_four_decimals(self) -> None:
        """Verify estimated_cost is rounded to 4 decimal places."""
        app = _make_app()
        # Use 1 token to get a tiny fractional cost
        stats = [_stat(model="gpt-4o", prompt_tokens=1, completion_tokens=1, total_tokens=2)]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        cost = resp.json()["All time"]["estimated_cost"]
        # The returned value should have at most 4 decimal places
        assert cost == round(cost, 4)

    def test_custom_model_costs_used(self) -> None:
        """Custom usage config model costs are applied."""
        custom_cfg = UsageConfig(model_costs={"custom-model": {"input": 1.0, "output": 2.0}})
        app = _make_app(usage_cfg=custom_cfg)
        stats = [_stat(model="custom-model", prompt_tokens=1_000_000, completion_tokens=1_000_000)]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        cost = resp.json()["All time"]["estimated_cost"]
        # 1M * $1.00 + 1M * $2.00 = $3.00
        assert cost == pytest.approx(3.0, abs=1e-4)

    def test_multi_model_cost_summed(self) -> None:
        """Costs across multiple models are summed."""
        app = _make_app()
        # gpt-4o: 1M prompt ($2.50) + gpt-4o-mini: 1M prompt ($0.15)
        cfg = UsageConfig()
        mini_input = cfg.model_costs.get("gpt-4o-mini", {}).get("input", 0.0)
        stats = [
            _stat(model="gpt-4o", prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000),
            _stat(model="gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000),
        ]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        cost = resp.json()["All time"]["estimated_cost"]
        expected = round(2.50 + mini_input, 4)
        assert cost == pytest.approx(expected, abs=1e-4)

    def test_none_model_name_treated_as_unknown(self) -> None:
        """A None model value should not raise and should resolve to zero cost."""
        app = _make_app()
        stats = [
            {
                "model": None,
                "prompt_tokens": 1_000_000,
                "completion_tokens": 1_000_000,
                "total_tokens": 2_000_000,
                "message_count": 1,
            }
        ]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        cost = resp.json()["All time"]["estimated_cost"]
        assert cost == 0.0


# ---------------------------------------------------------------------------
# UsageConfig custom period widths applied in since calculation
# ---------------------------------------------------------------------------


class TestCustomPeriodWidths:
    def test_custom_week_days_affects_since(self) -> None:
        """Custom week_days results in a different since value passed to storage."""
        default_app = _make_app(usage_cfg=UsageConfig(week_days=7))
        custom_app = _make_app(usage_cfg=UsageConfig(week_days=14))

        default_since = None
        custom_since = None

        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(default_app)
            client.get("/api/usage?period=week")
            default_since = mock_storage.get_usage_stats.call_args.kwargs.get("since")

        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(custom_app)
            client.get("/api/usage?period=week")
            custom_since = mock_storage.get_usage_stats.call_args.kwargs.get("since")

        assert default_since is not None
        assert custom_since is not None
        # 14-day window goes further back in time, so its since is an earlier timestamp
        assert custom_since < default_since

    def test_custom_month_days_affects_since(self) -> None:
        default_app = _make_app(usage_cfg=UsageConfig(month_days=30))
        custom_app = _make_app(usage_cfg=UsageConfig(month_days=60))

        default_since = None
        custom_since = None

        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(default_app)
            client.get("/api/usage?period=month")
            default_since = mock_storage.get_usage_stats.call_args.kwargs.get("since")

        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(custom_app)
            client.get("/api/usage?period=month")
            custom_since = mock_storage.get_usage_stats.call_args.kwargs.get("since")

        assert custom_since < default_since


# ---------------------------------------------------------------------------
# Response shape contract
# ---------------------------------------------------------------------------


class TestResponseShape:
    def test_by_model_entry_has_required_keys(self) -> None:
        app = _make_app()
        stats = [_stat()]
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = stats
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        entry = resp.json()["All time"]["by_model"][0]
        assert "model" in entry
        assert "prompt_tokens" in entry
        assert "completion_tokens" in entry
        assert "total_tokens" in entry
        assert "message_count" in entry

    def test_period_result_has_required_top_level_keys(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            resp = client.get("/api/usage?period=all")
        result = resp.json()["All time"]
        required_keys = (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "message_count",
            "estimated_cost",
            "by_model",
        )
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_content_type_is_json(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.usage.storage") as mock_storage:
            mock_storage.get_usage_stats.return_value = []
            client = TestClient(app)
            resp = client.get("/api/usage")
        assert "application/json" in resp.headers["content-type"]

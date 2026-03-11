from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.ui_scenarios.textual_scenarios import assert_matches_golden, render_scenario, scenario_names


def _run_render_scenario(name: str):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, render_scenario(name))
        return future.result()


@pytest.mark.parametrize("name", scenario_names())
def test_textual_ui_scenarios_match_goldens(name: str) -> None:
    rendered = _run_render_scenario(name)
    assert_matches_golden(rendered, update=os.getenv("UPDATE_TEXTUAL_GOLDENS") == "1")

"""Shared pytest configuration for repository test runs."""

from __future__ import annotations

import warnings


# Rich may decide this environment looks notebook-like and emit a late warning
# from rich.live after the test run has already completed. Ignore only that
# specific optional-Jupyter hint so focused verification runs stay quiet.
warnings.filterwarnings(
    "ignore",
    message=r'install "ipywidgets" for Jupyter support',
    category=UserWarning,
)

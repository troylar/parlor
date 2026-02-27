# Type Hints Required

All Python functions must include type hints for parameters and return values.

- Use `from __future__ import annotations` for deferred evaluation
- Use `typing` module types where needed: `Any`, `Optional`, `Union`, etc.
- Prefer `X | None` over `Optional[X]` (Python 3.10+)
- Dataclasses and TypedDict for structured data
- No `Any` without justification

# Test Requirements

> Vision principle: **"Lean over sprawling."** Tests prevent sprawl by catching regressions early. They're the safety net that lets the codebase stay lean — you can refactor aggressively when tests confirm nothing broke.

## New Code Must Have Tests

When adding or modifying code under `src/anteroom/`:

1. **New modules** (`src/anteroom/<name>.py` or `src/anteroom/<dir>/<name>.py`) MUST have a corresponding test file at `tests/unit/test_<name>.py`
2. **New public functions/methods** MUST have at least one unit test covering the happy path
3. **Bug fixes** MUST include a regression test that would have caught the bug
4. **Modified functions** — if you change behavior, update or add tests to cover the change

## Test Conventions

- Test files: `tests/unit/test_<module>.py`
- Test functions: `test_<function_name>_<scenario>()`
- Async tests: use `@pytest.mark.asyncio` (asyncio_mode is auto)
- Mock all external dependencies (DB, API calls, file I/O) in unit tests
- Use `pytest` fixtures, not setUp/tearDown
- Minimum coverage target: 80%

## What Doesn't Need Tests

- Private helper functions (tested indirectly through public API)
- Type definitions and dataclasses (unless they have methods with logic)
- Configuration constants
- `__init__.py` re-exports

## Before Committing

Run `pytest tests/unit/ -v --tb=short` and confirm all tests pass. Do not commit with failing tests.

# Developer Testing Guide

Anteroom has a multi-layered testing strategy covering unit tests, integration tests, end-to-end tests, prompt regression, agentic behavioral evals, adversarial red teaming, and reproducible demo scripts.

## Quick Reference

```bash
# Unit tests (fast, fully mocked)
pytest tests/unit/ -v

# Integration tests (real SQLite)
pytest tests/integration/ -v

# End-to-end tests (real servers, mock AI)
pytest tests/e2e/ -v

# Agent behavioral evals (real AI, slow)
pytest tests/e2e/ -m real_ai -v

# Prompt regression (requires running Anteroom + proxy)
npx promptfoo eval --config evals/promptfoo.yaml

# Red teaming (adversarial test generation)
npx promptfoo redteam run --config evals/redteam.yaml

# Demo recordings
cd demos && make demos

# Full pre-commit check
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest tests/unit/ -v
```

---

## Test Layers

### Layer 1: Unit Tests

**Location:** `tests/unit/` (~80 test files, 2,400+ tests)

Unit tests are the foundation. They are fully mocked — no I/O, no database, no network calls. Every new module under `src/anteroom/` must have a corresponding test file.

**Conventions:**

- File naming: `tests/unit/test_<module_name>.py`
- Function naming: `test_<function_name>_<scenario>()`
- Async tests: `@pytest.mark.asyncio` (auto mode in `pyproject.toml`)
- All external dependencies mocked (DB, API, file I/O)
- Use `pytest` fixtures, not `setUp`/`tearDown`

**Example patterns:**

```python
# Mocking the OpenAI client
from unittest.mock import AsyncMock, MagicMock

service = AIService.__new__(AIService)
service.client = MagicMock()
service.client.chat.completions.create = AsyncMock(return_value=mock_stream)

# Testing config loading with temp files
def test_config_loads_temperature(tmp_path):
    cfg_file = _write_config(tmp_path, {"ai": {"temperature": 0.5, ...}})
    config, _ = load_config(cfg_file)
    assert config.ai.temperature == 0.5

# Testing with environment variable overrides
def test_env_var_override(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CHAT_TEMPERATURE", "0.3")
    cfg_file = _write_config(tmp_path, {})
    config, _ = load_config(cfg_file)
    assert config.ai.temperature == 0.3
```

**Running:**

```bash
pytest tests/unit/ -v                          # All unit tests
pytest tests/unit/test_config.py -v            # Single file
pytest tests/unit/test_config.py::TestSamplingConfig -v  # Single class
pytest tests/unit/ -k "test_temperature"       # By name pattern
pytest tests/unit/ --cov=anteroom --cov-report=html  # With coverage
```

### Layer 2: Integration Tests

**Location:** `tests/integration/`

Integration tests use real SQLite databases to verify data flows across modules. They test the storage layer, query building, and schema migrations without mocking the database.

**Running:**

```bash
pytest tests/integration/ -v
```

### Layer 3: Contract Tests

**Location:** `tests/contract/`

Contract tests verify API endpoint request/response schemas match expectations. They ensure the REST API contract doesn't break when internal code changes.

### Layer 4: End-to-End Tests

**Location:** `tests/e2e/` (15 test files)

E2e tests start real Anteroom servers with real MCP servers but mock the AI service. They verify the full request flow: HTTP request &rarr; middleware &rarr; router &rarr; agent loop &rarr; tool execution &rarr; SSE response.

**Requirements:**

- `uvx` and/or `npx` on PATH (for MCP server tests)
- Tests skip gracefully when tools are unavailable

**Markers:**

- `@pytest.mark.e2e` — all end-to-end tests
- `requires_uvx` / `requires_npx` / `requires_mcp` — skip markers for missing tools

**Running:**

```bash
pytest tests/e2e/ -v                    # All e2e tests
pytest tests/e2e/ -m "e2e and not real_ai" -v  # E2e without real AI
```

### Layer 5: Agent Behavioral Evals

**Location:** `tests/e2e/test_agent_evals.py` (10 tests)

These tests invoke a **real AI backend** via `aroom exec --json` and verify the agent's decision-making: which tools it selects, whether it reads before writing, and whether it follows safety patterns.

**Requirements:**

- A configured AI backend (API key via `AI_CHAT_API_KEY` or `config.yaml`)
- Tests auto-skip when no API key is detected

**What they test:**

| Test Class | Tests | What it verifies |
|------------|-------|-----------------|
| `TestOutputStructure` | 3 | JSON schema, exit code 0, non-empty output |
| `TestToolSelection` | 3 | Uses `read_file`, `glob_files`, `grep` (not bash equivalents) |
| `TestReadBeforeWrite` | 1 | Reads/searches before suggesting changes |
| `TestMultiToolCoordination` | 1 | Uses multiple tools for codebase exploration |
| `TestSafetyCompliance` | 2 | No secret leakage, concise response style |

**Deterministic settings:** All tests run with `--temperature 0 --seed 42 --approval-mode auto` for maximum reproducibility.

**Important:** These tests are inherently non-deterministic because they call a real LLM. Assertions are structural (tool was used, output contains expected pattern) rather than exact string matches.

**Running:**

```bash
pytest tests/e2e/test_agent_evals.py -m real_ai -v
```

---

## Prompt Regression with promptfoo

**Location:** `evals/`

[promptfoo](https://github.com/promptfoo/promptfoo) is a declarative eval framework for testing LLM behavior. Anteroom's eval suite uses it to verify system prompt behaviors via the OpenAI-compatible proxy endpoint.

### Setup

1. Install promptfoo:
   ```bash
   npm install -g promptfoo
   # or use npx (no install needed)
   ```

2. Start Anteroom with proxy enabled and deterministic settings:
   ```bash
   AI_CHAT_PROXY_ENABLED=true AI_CHAT_TEMPERATURE=0 AI_CHAT_SEED=42 aroom
   ```

3. Get your auth token (from the `anteroom_session` browser cookie):
   ```bash
   export ANTEROOM_TOKEN="<token from DevTools > Application > Cookies>"
   ```

### Test Suites

#### `evals/promptfoo.yaml` — Prompt Regression (11 tests)

Tests system prompt behaviors via the proxy provider (`/v1/chat/completions`).

**Categories tested:**

| Category | Tests | What it verifies |
|----------|-------|-----------------|
| Communication style | 4 | Concise, direct, no preamble, no apologies, leads with answer |
| Safety behaviors | 3 | Refuses malware, resists prompt injection, won't leak system prompt |
| Code behaviors | 2 | Read-before-modify, edit-over-create |
| Tool selection | 2 | Prefers dedicated tools over bash equivalents |

**Assertion types used:**

- `contains` / `not-contains` — exact string presence/absence
- `contains-any` — at least one of a set
- `javascript` — custom JS assertions (output length, structural checks)
- `llm-rubric` — semantic grading by a judge model

**Running:**

```bash
# Basic run
npx promptfoo eval --config evals/promptfoo.yaml

# CI-friendly (no cache, no progress bar, JSON output)
npx promptfoo eval --config evals/promptfoo.yaml \
  --no-cache --no-progress-bar --no-table --output results.json

# View results in browser
npx promptfoo view
```

#### `evals/agentic.yaml` — Agentic Behavior (6 tests)

Tests the full agent loop using `aroom exec --json` as an exec provider. No proxy or auth token needed.

**What it tests:**

- Tool selection: uses `glob_files`, `read_file`, `grep`
- Safety gates: doesn't use bash for file reading
- Multi-step reasoning: reads files before suggesting changes
- Exit codes: completes with exit code 0

**Running:**

```bash
npx promptfoo eval --config evals/agentic.yaml
```

#### `evals/redteam.yaml` — Adversarial Testing

Auto-generates adversarial test cases targeting:

- Prompt injection and jailbreak attempts
- Privacy violations and PII extraction
- Cybercrime assistance requests
- Conversation hijacking

**Running:**

```bash
npx promptfoo redteam run --config evals/redteam.yaml
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTEROOM_TOKEN` | (required for proxy tests) | Bearer auth token |
| `ANTEROOM_BASE_URL` | `http://127.0.0.1:8080/v1` | Proxy base URL |
| `ANTEROOM_MODEL` | `gpt-4o` | Model name for provider config |

### CI Integration

promptfoo uses exit codes to gate CI:

- `0` — all tests passed
- `1` — execution error
- `100` — one or more test cases failed

```yaml
# GitHub Actions example
- name: Run prompt regression
  run: npx promptfoo eval --config evals/promptfoo.yaml --no-cache --no-progress-bar
  env:
    ANTEROOM_TOKEN: ${{ secrets.ANTEROOM_TOKEN }}
```

---

## Deterministic Output

For reproducible test results, configure temperature and seed:

### Config file

```yaml
ai:
  temperature: 0    # Greedy decoding (most deterministic)
  top_p: 1.0        # No nucleus sampling filtering
  seed: 42          # Provider-side determinism hint
```

### Environment variables

```bash
AI_CHAT_TEMPERATURE=0 AI_CHAT_TOP_P=1.0 AI_CHAT_SEED=42 aroom
```

### CLI flags

```bash
aroom exec --temperature 0 --seed 42 "your prompt"
aroom chat --temperature 0 --seed 42
```

**Caveats:**

- `temperature: 0` provides greedy decoding but exact reproducibility depends on the upstream provider
- `seed` improves consistency but is not guaranteed across all providers or hardware
- Some providers ignore `seed` entirely
- Structural assertions (tool used, pattern present) are more reliable than exact string matches

---

## Demo Recordings with VHS

**Location:** `demos/`

[VHS](https://github.com/charmbracelet/vhs) scripts produce reproducible terminal demo GIFs from `.tape` files.

### Setup

```bash
# macOS
brew install charmbracelet/tap/vhs ffmpeg ttyd

# Go
go install github.com/charmbracelet/vhs@latest
```

### Available Demos

| Demo | Script | Description |
|------|--------|-------------|
| Quickstart | `quickstart.tape` | Version check, basic chat, JSON output |
| Tool Usage | `tools.tape` | Agent using read_file, glob, grep |
| Exec Mode | `exec-mode.tape` | Scripting, JSON parsing, stdin piping |

### Building

```bash
cd demos
make demos          # Build all GIFs
make quickstart     # Build one demo
make clean          # Remove generated GIFs
```

### Writing New Demos

1. Create a new `.tape` file in `demos/`
2. Use `aroom exec` for non-interactive demos (avoids timing issues)
3. Use `--temperature 0 --seed 42` for deterministic output
4. Use `--approval-mode auto` for unattended tool execution
5. Set reasonable `Sleep` timers (8-12s for AI responses)
6. Run `vhs demos/your-demo.tape` to test

**Tape file reference:** [VHS Command Reference](https://github.com/charmbracelet/vhs#vhs-command-reference)

---

## Test Coverage Requirements

- **New modules** must have a test file at `tests/unit/test_<name>.py`
- **New public functions** must have at least one unit test (happy path)
- **Bug fixes** must include a regression test
- **Modified functions** need updated tests if behavior changed
- **Coverage target:** 80%+

### What Doesn't Need Tests

- Private helper functions (tested indirectly through public API)
- Type definitions and dataclasses (unless they have methods with logic)
- Configuration constants
- `__init__.py` re-exports
- Eval configs and demo scripts (tested by running them manually)

---

## Pytest Markers

| Marker | Description | When to use |
|--------|-------------|-------------|
| `@pytest.mark.asyncio` | Async test function | Any test with `async def` |
| `@pytest.mark.e2e` | End-to-end test | Tests starting real servers |
| `@pytest.mark.real_ai` | Requires real AI backend | Tests calling live LLM APIs |

Configure in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "e2e: end-to-end tests requiring real services",
    "real_ai: tests that call a real AI backend (require API key)",
]
```

---

## Summary: What to Run When

| Scenario | Command | Duration |
|----------|---------|----------|
| Before every commit | `pytest tests/unit/ -v && ruff check src/ tests/` | ~45s |
| Before submitting PR | `/submit-pr` (runs everything) | ~3min |
| After changing system prompt | `npx promptfoo eval --config evals/promptfoo.yaml` | ~2min |
| After changing tool selection logic | `pytest tests/e2e/ -m real_ai -v` | ~5min |
| Periodic security check | `npx promptfoo redteam run --config evals/redteam.yaml` | ~10min |
| After changing CLI output | `cd demos && make demos` | ~2min |

# Contributing

How to set up a development environment and contribute to Anteroom.

!!! tip
    See [Developer Workflow](developer-workflow.md) for the full Claude Code skills guide, commit conventions, and best practices.

## Development Setup

```bash
$ git clone https://github.com/troylar/anteroom.git
$ cd anteroom
$ pip install -e ".[dev]"
```

## Running Tests

```bash
$ pytest tests/ -v                           # All tests
$ pytest tests/unit/ -v                      # Unit tests (~2,400 tests)
$ pytest tests/integration/ -v               # Integration tests
$ pytest tests/e2e/ -v                       # E2e tests (requires uvx/npx)
$ pytest tests/e2e/ -m real_ai -v           # Agent evals (requires API key)
$ pytest --cov=anteroom --cov-report=html    # With coverage
```

See the [Developer Testing Guide](testing.md) for the full multi-layer testing strategy including promptfoo evals, red teaming, and VHS demo recordings.

## Linting and Formatting

```bash
$ ruff check src/ tests/        # Lint
$ ruff check src/ tests/ --fix  # Lint with auto-fix
$ ruff format src/ tests/       # Format (120 char line length)
```

## Code Style

- **Python**: 3.10+ (type hints, pattern matching)
- **Line length**: 120 characters
- **Formatter**: Ruff
- **Linter**: Ruff (rules: E, F, I, N, W)
- **Test framework**: pytest with `asyncio_mode = "auto"`
- **Coverage target**: 80%+

## Test Patterns

- **Unit tests** (`tests/unit/`): Fully mocked, no I/O
- **Integration tests** (`tests/integration/`): Real SQLite databases
- **Contract tests** (`tests/contract/`): API contract verification
- **E2e tests** (`tests/e2e/`): Real servers, mock AI (requires uvx/npx)
- **Agent evals** (`tests/e2e/test_agent_evals.py`): Real AI backend via `aroom exec --json`
- **Prompt regression** (`evals/`): promptfoo suites for system prompt behavior
- **Async tests**: Use `@pytest.mark.asyncio` with auto mode

## CI

GitHub Actions runs on every push and PR:

- Test matrix across Python 3.10--3.14
- Ruff lint + format check
- pytest with coverage
- pip-audit for dependency vulnerabilities
- Semgrep SAST scan (p/python + p/security-audit rulesets)
- CodeQL deep semantic analysis

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.10+, FastAPI, Uvicorn |
| **Frontend** | Vanilla JS (no build step), marked.js, highlight.js, KaTeX, DOMPurify |
| **CLI** | Rich, prompt-toolkit, tiktoken |
| **Database** | SQLite with FTS5, WAL journaling |
| **AI** | OpenAI Python SDK (async streaming) |
| **MCP** | Model Context Protocol SDK (stdio + SSE) |
| **Streaming** | Server-Sent Events (SSE) |
| **Typography** | Inter + JetBrains Mono (self-hosted WOFF2) |
| **Security** | OWASP ASVS L2, SRI, CSP, CSRF, rate limiting |

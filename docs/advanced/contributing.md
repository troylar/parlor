# Contributing

How to set up a development environment and contribute to Anteroom.

## Development Setup

```bash
$ git clone https://github.com/troylar/anteroom.git
$ cd anteroom
$ pip install -e ".[dev]"
```

## Running Tests

```bash
$ pytest tests/ -v              # All tests (~343 tests)
$ pytest tests/unit/ -v         # Unit tests only
$ pytest tests/integration/ -v  # Integration tests
$ pytest --cov=aroom --cov-report=html  # With coverage
```

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
- **Async tests**: Use `@pytest.mark.asyncio` with auto mode

## CI

GitHub Actions runs on every push and PR:

- Test matrix across Python 3.10--3.14
- Ruff lint + format check
- pytest with coverage
- pip-audit for dependency vulnerabilities
- Snyk SCA + SAST scans

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
| **Security** | OWASP ASVS L1, SRI, CSP, CSRF, rate limiting |

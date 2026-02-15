Write or update Parlor documentation pages.

## Arguments

The first argument is the doc page path (e.g., `cli/tools.md`). Any additional text describes what to write or update.

## Instructions

You are updating the Parlor documentation site built with MkDocs Material. Follow these steps:

### 1. Read the Target Page

If the page exists at `docs/$ARGUMENTS`, read it. If it doesn't exist, you'll create it from scratch.

### 2. Read Source Code

Read the corresponding source code to ensure documentation accuracy:

- CLI pages → `src/parlor/cli/`
- Web UI pages → `src/parlor/routers/`, `src/parlor/static/`
- Configuration → `src/parlor/config.py`
- Security → `src/parlor/app.py`, `src/parlor/tools/security.py`
- API pages → `src/parlor/routers/`
- Tools → `src/parlor/tools/`
- Agent loop → `src/parlor/services/agent_loop.py`

### 3. Write the Documentation

Follow these style conventions strictly:

- **Voice**: Direct, second-person ("you"), active voice
- **Tone**: Professional but approachable. No filler, no "In this section you will learn..."
- **Opening**: Every page starts with a one-sentence summary, then dives straight in
- **Code examples**: Every concept gets a working example. Use `$ ` prefix for shell commands
- **Tabbed blocks**: Use `pymdownx.tabbed` for multi-option examples (e.g., different API providers)
- **Admonitions**: Use `!!! tip`, `!!! warning`, `!!! info`, `!!! example` from MkDocs Material
- **Cross-references**: Link between pages liberally. Use relative paths like `../cli/tools.md`
- **Tables**: Use for reference material, feature comparisons, parameter lists
- **No emojis** unless explicitly requested

### 4. Verify

Run `mkdocs build --strict` to check for broken links, missing pages, and warnings.

```bash
mkdocs build --strict
```

Fix any issues found before finishing.

### 5. Cross-Reference Check

Ensure new pages are listed in `mkdocs.yml` under the `nav` section. Ensure links to and from the new page are consistent.

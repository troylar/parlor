---
name: write-docs
description: Write or update Anteroom documentation pages
allowed-tools: Bash, Read, Edit, Grep, Glob
---

Write or update Anteroom documentation pages.

## Arguments

The first argument is the doc page path (e.g., `cli/tools.md`). Any additional text describes what to write or update.

## Instructions

You are updating the Anteroom documentation site built with MkDocs Material. Follow these steps:

### 1. Read the Target Page

If the page exists at `docs/$ARGUMENTS`, read it. If it doesn't exist, you'll create it from scratch.

### 2. Read Source Code

Read the corresponding source code to ensure documentation accuracy:

- CLI pages → `src/anteroom/cli/`
- Web UI pages → `src/anteroom/routers/`, `src/anteroom/static/`
- Configuration → `src/anteroom/config.py`
- Security → `src/anteroom/app.py`, `src/anteroom/tools/security.py`
- API pages → `src/anteroom/routers/`
- Tools → `src/anteroom/tools/`
- Agent loop → `src/anteroom/services/agent_loop.py`
- Embeddings → `src/anteroom/services/embeddings.py`
- Canvas → `src/anteroom/tools/canvas.py`

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

### 4. Verify

Run `mkdocs build --strict` to check for broken links, missing pages, and warnings.

```bash
mkdocs build --strict
```

Fix any issues found before finishing.

### 5. Cross-Reference Check

Ensure new pages are listed in `mkdocs.yml` under the `nav` section. Ensure links to and from the new page are consistent.

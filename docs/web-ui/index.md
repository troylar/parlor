# Web UI

Anteroom's web interface is a self-hosted ChatGPT-style chat UI that runs entirely on your machine.

```bash
$ aroom
```

Your browser opens to `http://127.0.0.1:8080`.

![Anteroom Web UI](../screenshots/theme-midnight.png)

## Features at a Glance

| Feature | Description |
|---|---|
| [Conversations](conversations.md) | Create, search, fork, rewind, edit, export |
| [Projects](projects.md) | Group conversations with custom system prompts and models |
| [Streaming](streaming.md) | Token-by-token SSE streaming with prompt queue |
| [Themes](themes.md) | 4 built-in themes with instant switching |
| [Keyboard Shortcuts](keyboard-shortcuts.md) | Command palette, shortcuts, settings |
| [Attachments](attachments.md) | 35+ file types with magic-byte verification |
| [Shared Databases](shared-databases.md) | Multiple SQLite databases for team or topic separation |

## Rich Rendering

Anteroom renders AI responses with full Markdown support:

| Format | Support |
|---|---|
| **Markdown** | Full GFM --- tables, lists, blockquotes, strikethrough, task lists |
| **Code blocks** | Syntax highlighting via highlight.js with language label + one-click copy |
| **LaTeX math** | Inline `$x^2$` / `\(x^2\)` and display `$$\int$$` / `\[\int\]` via KaTeX |
| **Images** | Inline previews for attached images |
| **HTML subset** | `<kbd>`, `<sup>`, `<sub>`, `<dl>`/`<dt>`/`<dd>` via DOMPurify allowlist |

## Responsive Design

| Breakpoint | Target | Behavior |
|---|---|---|
| **1400px+** | Large desktop | Wider messages (900px), expanded sidebar (300px) |
| **769--1399px** | Desktop | Default layout |
| **768--1024px** | Tablet | Compact sidebar (240px), full-width messages |
| **0--767px** | Mobile | Slide-over sidebar with hamburger menu + dark overlay |

## Settings

Click the gear icon in the sidebar to open settings:

- **Model selector** --- dropdown populated live from your API
- **System prompt editor** --- change at runtime, persists to `config.yaml`
- **Theme picker** --- visual cards showing each theme's color palette

Changes take effect immediately, no restart needed.

## MCP Tool Integration

Connect [MCP servers](../configuration/mcp-servers.md) and the AI gains access to external tools. Tool calls render as expandable detail panels showing input during execution and output + status when complete.

- Spinner animation while tools execute
- Connected server count and total tool count shown in sidebar footer
- SSRF protection with DNS resolution and shell metacharacter rejection

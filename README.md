<p align="center">
  <img src="https://img.shields.io/pypi/v/parlor?style=for-the-badge&color=3b82f6&labelColor=0f1117" alt="PyPI Version">
  <img src="https://img.shields.io/pypi/pyversions/parlor?style=for-the-badge&color=10b981&labelColor=0f1117" alt="Python Versions">
  <img src="https://img.shields.io/github/actions/workflow/status/troylar/parlor/test.yml?style=for-the-badge&label=tests&color=7c3aed&labelColor=0f1117" alt="Tests">
  <img src="https://img.shields.io/github/license/troylar/parlor?style=for-the-badge&color=e8913a&labelColor=0f1117" alt="License">
</p>

<h1 align="center">
  <br>
  Parlor
  <br>
</h1>

<h3 align="center">A private parlor for AI conversation.</h3>

<p align="center">
  Self-hosted ChatGPT-style web UI that connects to any OpenAI-compatible API.<br>
  <strong>Install with pip. Run locally. Own your data.</strong>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> &bull;
  <a href="#-features">Features</a> &bull;
  <a href="#-themes">Themes</a> &bull;
  <a href="#-security">Security</a> &bull;
  <a href="#-api-reference">API</a>
</p>

<p align="center">
  <img src="docs/screenshots/theme-midnight.png" alt="Parlor - Midnight Theme" width="800">
</p>

---

## Why Parlor?

Your company's AI chat UI sucks. You know it. We know it. Parlor replaces it with something you'll actually want to use.

It connects to **any** OpenAI-compatible endpoint --- your company's internal API, OpenAI, Azure, Ollama, LM Studio, or anything else that speaks the OpenAI protocol. Built to [OWASP ASVS L1](SECURITY.md) standards because your conversations deserve real security, not security theater.

> **One command. No cloud. No telemetry. No compromise.**

```bash
pip install parlor
```

---

## :rocket: Quick Start

**1. Install**

```bash
pip install parlor
```

**2. Configure** --- create `~/.ai-chat/config.yaml`:

```yaml
ai:
  base_url: "https://your-ai-endpoint/v1"
  api_key: "your-api-key"
  model: "gpt-4"
```

**3. Verify** your connection:

```bash
parlor --test
```

**4. Launch:**

```bash
parlor
```

Your browser opens to `http://127.0.0.1:8080`. That's it. You're done. :tada:

---

## :sparkles: Features

### :speech_balloon: Conversations

| | |
|---|---|
| **Create, rename, search, delete** | Full conversation lifecycle with double-click rename |
| :mag: **Full-text search** | FTS5-powered instant search across all messages and titles |
| :trident: **Fork at any message** | Branch a conversation into a new thread from any point |
| :pencil2: **Edit & regenerate** | Edit any user message, all subsequent messages are deleted, AI regenerates from there |
| :arrow_down: **Export to Markdown** | One-click download of any conversation as `.md` |
| :bulb: **Auto-titles** | AI generates a title from your first message |
| :repeat: **Per-conversation model** | Switch models mid-conversation from the top bar dropdown |
| :floppy_disk: **Copy between databases** | Duplicate an entire conversation (with messages + tool calls) to another database |

### :file_folder: Projects

Group conversations under projects with **custom system prompts** and **per-project model selection**. Your coding project uses Claude with a developer prompt. Your writing project uses GPT-4 with an editorial voice. Each project is its own world. :earth_americas:

- :memo: Project-scoped system prompt overrides the global default
- :robot: Per-project model override (or "use global default")
- :open_file_folder: Project-scoped folders --- each project gets its own folder hierarchy
- :shield: Deleting a project preserves its conversations (they become unlinked, not deleted)
- :eyes: "All Conversations" view to see everything across projects

### :card_index_dividers: Organization

<table>
<tr>
<td width="50%">

**:file_folder: Folders**
- :deciduous_tree: Nested folder hierarchy with unlimited depth
- :heavy_plus_sign: Add subfolders from the folder context menu
- :floppy_disk: Collapse/expand state persists to the database
- :left_right_arrow: Depth-based indentation in the sidebar
- :wastebasket: Rename and delete (conversations are preserved, not deleted)
- :lock: Project-scoped: each project gets its own folder tree

</td>
<td width="50%">

**:label: Tags**
- :art: Color-coded labels on conversations (hex color picker)
- :heavy_plus_sign: Create tags inline from any conversation's tag dropdown
- :mag_right: Filter the sidebar by tag
- :round_pushpin: Visual badges with color indicators
- :broom: Delete a tag and it's cleanly removed from all conversations

</td>
</tr>
</table>

### :link: Shared Databases

Connect **multiple SQLite databases** for team or topic-based separation. Each database is fully independent --- its own conversations, attachments, and history.

- :open_file_folder: **Visual file browser** with directory navigation for selecting `.db`/`.sqlite`/`.sqlite3` files
- :arrows_counterclockwise: **Copy conversations** between databases (full message + tool call history)
- :electric_plug: **Switch databases** from the sidebar --- active database is visually indicated
- :abc: Database names: letters, numbers, hyphens, underscores only
- :house: "personal" database always exists and can't be removed
- :locked: Paths restricted to your home directory for security

### :art: Rich Rendering

| Format | Support |
|---|---|
| :page_facing_up: **Markdown** | Full GFM --- tables, lists, blockquotes, strikethrough, task lists |
| :computer: **Code blocks** | Syntax highlighting via highlight.js with language label + one-click copy button |
| :1234: **LaTeX math** | Inline `$x^2$` / `\(x^2\)` and display `$$\int$$` / `\[\int\]` via KaTeX |
| :framed_picture: **Images** | Inline previews for attached images |
| :globe_with_meridians: **HTML subset** | `<kbd>`, `<sup>`, `<sub>`, `<dl>`/`<dt>`/`<dd>` via DOMPurify allowlist |

### :paperclip: File Attachments

Drag-and-drop or click to attach. **35+ file types** supported. Up to **10 files per message**, **10 MB each**. Every file is verified with magic-byte detection --- a renamed `.exe` won't sneak through as a `.png`. :no_entry_sign:

| Category | Extensions |
|---|---|
| :keyboard: **Code** | `.py` `.js` `.ts` `.java` `.c` `.cpp` `.h` `.hpp` `.rs` `.go` `.rb` `.php` `.sh` `.bat` `.ps1` `.sql` `.css` |
| :bar_chart: **Data** | `.json` `.yaml` `.yml` `.csv` `.xml` `.toml` `.ini` `.cfg` `.log` |
| :page_with_curl: **Documents** | `.txt` `.md` `.pdf` |
| :camera: **Images** | `.png` `.jpg` `.jpeg` `.gif` `.webp` |

- :frame_photo: Image attachments show inline thumbnails with file size
- :arrow_down: Non-image files force-download (never rendered in-browser)
- :broom: Filenames are sanitized: path components stripped, special characters replaced

### :hammer_and_wrench: MCP Tool Integration

Connect **stdio** or **SSE-based** MCP servers. Your AI gains access to external tools --- databases, APIs, file systems, anything with an MCP adapter. :zap:

- :gear: Tool calls render as **expandable detail panels** --- see input during execution, output + status when complete
- :hourglass_flowing_sand: Spinner animation while tools execute
- :signal_strength: Connected server count and total tool count shown in sidebar footer
- :shield: SSRF protection with DNS resolution and shell metacharacter rejection on tool args

```yaml
mcp_servers:
  - name: "my-tools"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@my-org/mcp-tools"]

  - name: "remote-tools"
    transport: "sse"
    url: "https://mcp-server.example.com/sse"
```

### :ocean: Streaming

Real-time **token-by-token streaming** via Server-Sent Events.

- :fast_forward: Markdown and math render live as tokens arrive
- :eye: **Raw mode toggle** (eye icon in top bar) --- view unprocessed text during streaming, persists across sessions
- :stop_sign: Stop generation mid-response with `Escape` or the stop button
- :thought_balloon: Animated thinking indicator with pulsing dots while AI processes
- :rotating_light: Error messages show inline with a **Retry** button

### :zap: Command Palette

**`Cmd+K`** / **`Ctrl+K`** opens a Raycast-style command palette with fuzzy matching.

| Command type | What it does |
|---|---|
| :new: **New Chat** | Create a fresh conversation |
| :art: **Theme: Midnight / Dawn / Aurora / Ember** | Switch themes instantly |
| :robot: **Model names** | Switch the current model (all available models listed) |
| :file_folder: **Project names** | Jump to a project |
| :clock1: **Recent conversations** | Quick-jump to your 10 most recent chats |

Arrow keys to navigate, `Enter` to select, `Escape` to dismiss.

### :keyboard: Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Cmd/Ctrl + K` | :zap: Open command palette |
| `Ctrl + Shift + N` | :new: New conversation |
| `Escape` | :stop_sign: Stop generation / close palette / close modal |
| `Enter` | :arrow_right: Send message |
| `Shift + Enter` | :leftwards_arrow_with_hook: Newline in message input |

### :gear: Settings UI

Click the gear icon in the sidebar to open the settings modal:

- :robot: **Model selector** --- dropdown populated live from your API
- :memo: **System prompt editor** --- change at runtime, persists to `config.yaml`
- :art: **Theme picker** --- visual cards showing each theme's color palette
- :white_check_mark: Changes take effect immediately, no restart needed

---

## :palette: Themes

Four built-in themes, each with a distinct visual identity. Switch instantly via settings or command palette (`Cmd+K`). :sparkles:

### :crescent_moon: Midnight `Default`

Premium tech dark --- think Linear, Raycast, Vercel. Deep navy-charcoal with electric blue accents. Glassmorphic sidebar.

<p align="center">
  <img src="docs/screenshots/theme-midnight.png" alt="Midnight Theme" width="800">
</p>

### :sunrise: Dawn `Light`

Warm editorial light --- think Notion in sunlight. Cream backgrounds, soft indigo-violet accents, subtle paper texture.

<p align="center">
  <img src="docs/screenshots/theme-dawn.png" alt="Dawn Theme" width="800">
</p>

### :milky_way: Aurora `Showstopper`

Living gradient dark with animated CSS aurora (purple/teal/emerald). Gradient borders, animated input focus rings.

<p align="center">
  <img src="docs/screenshots/theme-aurora.png" alt="Aurora Theme" width="800">
</p>

### :fire: Ember `Cozy`

Warm luxury dark --- amber by firelight. Brown-charcoal backgrounds, rich amber glow on focus states.

<p align="center">
  <img src="docs/screenshots/theme-ember.png" alt="Ember Theme" width="800">
</p>

**Visual details:**
- :crystal_ball: Glassmorphism with `backdrop-filter: blur(20px)` on sidebar
- :new_moon_with_face: Multi-layered shadows for depth: `0 1px 2px` + `0 4px 12px`
- :dizzy: Micro-animations: sidebar items shift on hover, buttons glow, modals spring in
- :rainbow: Gradient text effect on welcome heading
- :performing_arts: Smooth 0.5s cross-fade transition between themes
- :clipboard: Code block copy button fades in on hover
- :floppy_disk: Theme persists in localStorage across sessions --- no flash on reload

---

## :iphone: Responsive Design

| Breakpoint | Target | Behavior |
|---|---|---|
| **1400px+** | :desktop_computer: Large desktop | Wider messages (900px), expanded sidebar (300px) |
| **769-1399px** | :computer: Desktop | Default layout |
| **768-1024px** | :tablet: Tablet | Compact sidebar (240px), full-width messages |
| **0-767px** | :iphone: Mobile | Slide-over sidebar with hamburger menu + dark overlay |

Mobile sidebar slides in with `transform` animation. Tap the overlay or hamburger to dismiss.

---

## :wrench: Configuration

### Config File

`~/.ai-chat/config.yaml`

```yaml
ai:
  base_url: "https://your-ai-endpoint/v1"
  api_key: "your-api-key"
  model: "gpt-4"
  system_prompt: "You are a helpful assistant."
  verify_ssl: true  # set false for self-signed certs

app:
  host: "127.0.0.1"     # bind address
  port: 8080             # server port
  data_dir: "~/.ai-chat" # where DB + attachments live

# Optional: shared databases
shared_databases:
  - name: "team-shared"
    path: "~/shared/team.db"

# Optional: MCP tool servers
mcp_servers:
  - name: "my-tools"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@my-org/mcp-tools"]

  - name: "remote-tools"
    transport: "sse"
    url: "https://mcp-server.example.com/sse"
```

### :earth_americas: Environment Variables

Every config option has an env var override:

| Variable | Default | Description |
|---|---|---|
| `AI_CHAT_BASE_URL` | --- | :electric_plug: AI API endpoint **(required)** |
| `AI_CHAT_API_KEY` | --- | :key: API key **(required)** |
| `AI_CHAT_MODEL` | `gpt-4` | :robot: Model name |
| `AI_CHAT_SYSTEM_PROMPT` | `You are a helpful assistant.` | :memo: System prompt |
| `AI_CHAT_VERIFY_SSL` | `true` | :lock: SSL certificate verification |

---

## :terminal: CLI

```
parlor              Launch server and open browser
parlor --test       Test connection, list models, send test prompt, exit
parlor --help       Show help
```

<details>
<summary><strong>:test_tube: Example <code>--test</code> output</strong></summary>

```
Config:
  Endpoint: https://your-ai-endpoint/v1
  Model:    gpt-4
  SSL:      enabled

1. Listing models...
   OK - 12 model(s) available
     - gpt-4
     - gpt-4-turbo
     - gpt-3.5-turbo
     ...

2. Sending test prompt to gpt-4...
   OK - Response: Hello! How can I help you today?

All checks passed.
```

</details>

---

## :shield: Security

Parlor is hardened for use on corporate networks and shared machines. Not a checkbox exercise --- real, layered defense. :lock:

| Layer | What it does |
|---|---|
| :key: **Authentication** | Random session token, HttpOnly cookies, HMAC-SHA256 timing-safe comparison |
| :shield: **CSRF** | Per-session tokens validated on all state-changing requests |
| :no_entry: **CSP** | `script-src 'self'`, `frame-ancestors 'none'`, no inline scripts |
| :page_with_curl: **Security Headers** | X-Frame-Options DENY, X-Content-Type-Options nosniff, strict Referrer-Policy, Permissions-Policy |
| :floppy_disk: **Database** | Column-allowlisted SQL builder, parameterized queries everywhere, `0600` file permissions, path validation |
| :broom: **Input Sanitization** | DOMPurify on all rendered HTML, UUID validation on all IDs, filename sanitization |
| :traffic_light: **Rate Limiting** | 120 req/min per IP with LRU eviction |
| :weight_lifting: **Body Size** | 15 MB max request |
| :globe_with_meridians: **CORS** | Locked to configured origin, explicit method/header allowlist |
| :paperclip: **File Safety** | MIME type allowlist + magic-byte verification, path traversal prevention, forced download for non-images |
| :hammer_and_wrench: **MCP Safety** | SSRF protection with DNS resolution, shell metacharacter rejection in tool args |
| :hash: **SRI** | SHA-384 hashes on all vendor scripts |
| :see_no_evil: **API Surface** | OpenAPI/Swagger docs disabled |

Full details in [SECURITY.md](SECURITY.md).

---

## :file_cabinet: Data Storage

Everything stays on your machine. Nothing phones home. :lock:

```
~/.ai-chat/
  config.yaml          # Configuration          (permissions: 0600)
  chat.db              # SQLite + WAL journal   (permissions: 0600)
  attachments/         # Files by conversation  (permissions: 0700)
```

The data directory is created with `0700` permissions (owner-only). Database files are created with `0600` permissions. WAL and SHM sidecar files are locked down too.

---

## :books: API Reference

Parlor exposes a full REST API. All endpoints require authentication via session cookie + CSRF token. :key:

<details>
<summary><strong>:speech_balloon: Conversations</strong></summary>

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/conversations` | List (with `?search=`, `?project_id=`, `?db=`) |
| `POST` | `/api/conversations` | Create |
| `GET` | `/api/conversations/:id` | Get with messages, attachments, and tool calls |
| `PATCH` | `/api/conversations/:id` | Update title, folder, model |
| `DELETE` | `/api/conversations/:id` | Delete with all attachments |
| `GET` | `/api/conversations/:id/export` | Export as Markdown |
| `POST` | `/api/conversations/:id/chat` | Stream chat (SSE) |
| `POST` | `/api/conversations/:id/stop` | Cancel active generation |
| `POST` | `/api/conversations/:id/fork` | Fork at a message position |
| `POST` | `/api/conversations/:id/copy` | Copy to another database (`?target_db=`) |

</details>

<details>
<summary><strong>:envelope: Messages & Attachments</strong></summary>

| Method | Endpoint | Description |
|---|---|---|
| `PUT` | `/api/messages/:id` | Edit message content (deletes subsequent messages) |
| `DELETE` | `/api/messages/:id` | Delete messages after a position |
| `GET` | `/api/attachments/:id` | Download attachment file |

</details>

<details>
<summary><strong>:file_folder: Projects</strong></summary>

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/projects` | List all projects |
| `POST` | `/api/projects` | Create project (name, instructions, model) |
| `PATCH` | `/api/projects/:id` | Update name, instructions, or model |
| `DELETE` | `/api/projects/:id` | Delete project (conversations preserved) |

</details>

<details>
<summary><strong>:card_index_dividers: Folders</strong></summary>

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/folders` | List folders (`?project_id=` to filter) |
| `POST` | `/api/folders` | Create folder (name, parent_id, project_id) |
| `PATCH` | `/api/folders/:id` | Update name, parent, collapsed state, position |
| `DELETE` | `/api/folders/:id` | Delete folder + subfolders (conversations preserved) |

</details>

<details>
<summary><strong>:label: Tags</strong></summary>

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/tags` | List all tags |
| `POST` | `/api/tags` | Create tag (name, color) |
| `PATCH` | `/api/tags/:id` | Update name or color |
| `DELETE` | `/api/tags/:id` | Delete tag (removed from all conversations) |
| `POST` | `/api/conversations/:id/tags/:tag_id` | Add tag to conversation |
| `DELETE` | `/api/conversations/:id/tags/:tag_id` | Remove tag from conversation |

</details>

<details>
<summary><strong>:link: Databases</strong></summary>

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/databases` | List all connected databases |
| `POST` | `/api/databases` | Add database (name, path) |
| `DELETE` | `/api/databases/:name` | Remove database connection |
| `GET` | `/api/browse?path=` | Browse filesystem for `.db`/`.sqlite`/`.sqlite3` files |

</details>

<details>
<summary><strong>:gear: Config & Models</strong></summary>

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/config` | Get current config + MCP server statuses |
| `PATCH` | `/api/config` | Update model and/or system prompt |
| `POST` | `/api/config/validate` | Test API connection, list models |
| `GET` | `/api/models` | List available models (sorted) |
| `GET` | `/api/mcp/tools` | List all available MCP tools with schemas |

</details>

---

## :hammer_and_pick: Development

```bash
git clone https://github.com/troylar/parlor.git
cd parlor
pip install -e ".[dev]"

pytest tests/ -v          # Run 100 tests
ruff check src/ tests/    # Lint
ruff format src/ tests/   # Format
```

### :toolbox: Tech Stack

| | |
|---|---|
| :snake: **Backend** | Python 3.10+, FastAPI, Uvicorn |
| :globe_with_meridians: **Frontend** | Vanilla JS (no build step), marked.js, highlight.js, KaTeX, DOMPurify |
| :floppy_disk: **Database** | SQLite with FTS5 full-text search, WAL journaling |
| :brain: **AI** | OpenAI Python SDK (async streaming) |
| :hammer_and_wrench: **MCP** | Model Context Protocol SDK (stdio + SSE transports) |
| :ocean: **Streaming** | Server-Sent Events (SSE) |
| :pencil: **Typography** | Inter + JetBrains Mono (self-hosted WOFF2, zero external requests) |
| :shield: **Security** | OWASP ASVS L1 compliance, SRI, CSP, CSRF, rate limiting |

---

<p align="center">
  <strong>MIT License</strong><br>
  Built with :heart: for people who care about their conversations.
</p>

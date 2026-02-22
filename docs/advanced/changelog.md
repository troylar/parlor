# Changelog

Release highlights for every Anteroom version. For full details including developer notes and upgrade instructions, see the linked GitHub Release.


---

## February 22, 2026

### v1.24.6

**Fixed:**

- **Changelog rendering on ReadTheDocs**: Section headers were rendering inline with bullet points — added blank lines for proper MkDocs Material list rendering (#295)
- Completed ~45 truncated changelog entries from initial backfill (#295)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.6)

### v1.24.5

**Improved:**

- **Changelog readability**: Release entries now grouped by type — New, Fixed, Improved — for easier scanning (#295)
- **Changelog navigation**: Moved to top-level nav in docs instead of hidden under Advanced (#295)
- Deploy skill auto-generates future entries in the new type-segregated format (#295)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.5)

### v1.24.4

**New:**

- **Changelog with release highlights**: Every release now has a highlights entry in `docs/advanced/changelog.md`, viewable on ReadTheDocs (#290)
- Backfilled all 80 existing releases; `/deploy` skill auto-appends new entries going forward

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.4)

### v1.24.3

**Improved:**

- **Replaced Snyk with Semgrep + CodeQL** for open-source SAST scanning — no more external tokens or Node.js required in CI (#289)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.3)

### v1.24.2

**Fixed:**

- Fixed MCP tool argument validation that incorrectly blocked legitimate text content (newlines, parentheses, semicolons, etc.) when passed to MCP servers like filesystem or database tools (#291)
- Improved MCP tool error messages to include server name and tool context for easier debugging (#291)
- Sanitized MCP error output so raw server exceptions are logged server-side only, not exposed to the user (#291)

**Improved:**

- Updated README.md MCP safety description to reflect current behavior (#291)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.2)

### v1.24.1

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.1)

### v1.24.0

**New:**

- **OpenAI-compatible proxy endpoint**: External tools using the OpenAI SDK can route requests through Anteroom to the upstream API (#285)
- Endpoints: `GET /v1/models` and `POST /v1/chat/completions` with full streaming support
- Opt-in via `proxy.enabled: true` — disabled by default for security

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.0)

### v1.23.0

**New:**

- **Iterative plan refinement**: `/plan edit` opens in `$EDITOR`, `/plan reject` triggers AI revision (#270, #271)
- **Inline planning**: `/plan <prompt>` enters planning mode in one command (#265)
- Auto-plan suggestions when tasks exceed tool-call threshold (#265)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.23.0)

### v1.22.1

**New:**

- **Planning Mode**: AI can now generate a structured step-by-step plan before executing tasks. Start planning mode with `aroom chat --plan` or `/plan on` during a session (#264)
- **Plan Editing**: Open your plan in `$VISUAL`/`$EDITOR` with `/plan edit` to review and modify it before approving execution. (#270)

**Improved:**

- Added a feature parity development rule ensuring all new features work equivalently in both the CLI and web UI (#275)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.22.1)


---

## February 21, 2026

### v1.22.0

**New:**

- **Built-in `/docs` skill**: Look up Anteroom documentation without leaving the CLI — covers config, flags, tools, skills, and architecture (#262)
- Embeds quick-reference tables for instant answers; consults 42 documentation files for deeper questions

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.22.0)

### v1.21.0

**New:**

- **Non-interactive exec mode**: `aroom exec "prompt"` for scripting and CI pipelines (#232)
- Supports stdin piping, `--json` output, timeout control, and conversation persistence

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.21.0)

### v1.20.1

**Fixed:**

- Fixed stale thinking line text persisting after stream retry — the "Stream timed out" and "retrying in Ns" text no longer flashes or leaves ghost content on the thinking line (#253)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.20.1)

### v1.20.0

**New:**

- **ANTEROOM.md Project Conventions**: Anteroom now formally supports `ANTEROOM.md` as a project-level conventions file that the AI follows consistently across both CLI and web UI (#215)
- Auto-discovers conventions walking up from your working directory (#215)
- **Web UI now loads ANTEROOM.md** — previously CLI-only, conventions now apply in both interfaces (#215)

**Improved:**

- Removed legacy `PARLOR.md` instruction filename support — use `ANTEROOM.md` going forward (#215)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.20.0)


---

## February 20, 2026

### v1.19.0

**New:**

- **Trust Prompts for Project Instructions**: Anteroom now prompts you before loading project-level `ANTEROOM.md` files into the AI context. This prevents prompt injection from untrusted project directories (#219)
- Trust decisions are persisted with SHA-256 content hash verification — you only need to approve once per project (#219)
- If the file changes, you'll be re-prompted to review and approve the new content (#219)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.19.0)

### v1.18.4

**Fixed:**

- **Fixed CLI hang after pressing Escape** — pressing Escape to cancel a running command could leave the CLI unresponsive, requiring a force-quit. The REPL now cleanly cancels the active operation and returns to the prompt (#243)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.4)

### v1.18.3

**Fixed:**

- **API error handling during streaming** — Previously, HTTP errors from the AI provider (like 500 Internal Server Error, 502 Bad Gateway, or 404 Not Found) could crash the stream or produce confusing output. Now handled gracefully with clear error messages (#241)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.3)

### v1.18.2

**Fixed:**

- Fixed the CLI thinking indicator ("Thinking...") briefly flashing then dropping to a blank line on the very first message in a new REPL session. Subsequent messages were unaffected (#239)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.2)

### v1.18.1

**Fixed:**

- **Fixed timeout enforcement on API connections** — The configured `request_timeout` (default 120s) was not enforced as a hard deadline during the initial API connection phase, allowing slow connections to hang indefinitely (#237)
- **Fixed Escape key ignored during connecting phase** — Pressing Escape while the API was connecting had no effect until the connection completed or timed out. Now cancels immediately (#237)
- **Fixed cancel-during-retry loop** — If the user pressed Escape during a retry backoff delay, the retry loop could re-enter the connection attempt instead of returning to the prompt (#237)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.1)

### v1.18.0

**New:**

- **Configurable Timeouts and Thresholds**: Every timeout, threshold, and limit that was previously hardcoded is now a config field with a sensible default (#235)
- `write_timeout` — time to send request body (default: 30s)
- `pool_timeout` — wait for free connection from pool (default: 10s)

**Fixed:**

- **Escape during stalled stream now cancels cleanly** — Previously, pressing Escape while a stream was stalled would trigger a retry countdown instead of cancelling. Now returns to the prompt immediately (#235)
- **Stalled streams abort faster** — Added per-chunk stall timeout (default 30s) so streams that go silent mid-response are aborted sooner instead of waiting for the full request timeout (#235)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.0)

### v1.17.1

**Fixed:**

- Fixed the CLI thinking spinner showing stale phase text ("waiting for first token", "streaming · N chars") and "esc to cancel" hint on the final line after a response completes (#231)
- Fixed the per-phase timer not always appearing during the "waiting for first token" phase. The timer now starts immediately when thinking begins, rather than waiting for the first phase transition (#231)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.17.1)

### v1.17.0

**New:**

- **Real-Time Connection Health Monitor for CLI**: The CLI thinking spinner now shows live connection status so you always know what's happening during AI interactions (#221)
- **Phase tracking**: See "connecting", "connected · waiting for first token", and "streaming · N chars" as the request progresses (#221)
- **Per-phase timing**: Each phase shows how long it's been active (e.g., "waiting for first token (5s)")

**Fixed:**

- Fixed error messages leaking internal API details — now shows generic "AI request error" instead of raw provider messages (#221)
- Fixed exception class names appearing in retry event payloads (#221)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.17.0)

### v1.16.3

**Fixed:**

- **Fixed confusing "HARD BLOCKED" message after approving dangerous commands.** Previously, when you approved a dangerous command like `rm -rf`, the system would still show a "HARD BLOCKED" error. Now bypasses the redundant safety check after explicit approval (#217)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.16.3)

### v1.16.2

**Fixed:**

- **Smarter API timeouts**: Replaced the single 120-second timeout with three phase-aware timeouts — connect (5s), first-token (30s), and stream (120s). This prevents long waits when the API is unreachable (#213)
- **Automatic retry on transient errors**: When the API times out or drops a connection, Anteroom now automatically retries up to 3 times with exponential backoff (#213)
- **Fixed phantom thinking timer after timeout**: Previously, if the API timed out, the thinking spinner would restart and keep counting up even after the error message was shown (#213)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.16.2)

### v1.16.1

**Fixed:**

- CLI no longer prints noisy tracebacks when pressing Ctrl+C with MCP servers connected. Shutdown errors are now suppressed from terminal output and logged at debug level (#208)
- Fixed a test that would fail when `AI_CHAT_API_KEY` was set in the shell environment (#208)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.16.1)

### v1.16.0

**New:**

- **Granular Request Lifecycle Phases in Thinking Indicator**: The thinking spinner now shows exactly where time is being spent during AI responses, making it easy to diagnose slow connections (#203)
- **Connecting** — shown while establishing connection to the AI API (#203)
- **Waiting for first token** — shown after the request is sent, while waiting for the model to start responding (#203)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.16.0)

### v1.15.0

**New:**

- **Rich Markdown in Resume Recap**: When resuming a conversation with `/last` or `/resume`, the assistant's last message is now rendered with full Rich Markdown formatting (#199)
- Long assistant messages truncate at line boundaries to preserve markdown structure
- Truncation limit increased from 300 to 500 characters for better context

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.15.0)

### v1.14.11

**Fixed:**

- **Thinking spinner no longer freezes during API stalls** — The CLI thinking timer previously stuck at "1s" when the API was slow to respond between tool calls. Now uses a background ticker task that updates independently (#197)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.11)

### v1.14.10

**New:**

- **`--port` flag**: Override the configured port directly from the command line
- **`AI_CHAT_PORT` environment variable**: Set a default port via environment variable, useful for scripts and containerized setups
- **Smarter browser launch**: The browser now waits until the server is actually ready before opening, preventing "connection refused" errors on slower startups

**Fixed:**

- **Port-in-use errors now show actionable guidance** — when port 8080 (or your configured port) is already taken, Anteroom now prints a clear message with the `--port` flag and `AI_CHAT_PORT` env var as alternatives (#193)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.10)

### v1.14.9

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.9)

### v1.14.8

**Fixed:**

- **Fixed thinking indicator hanging indefinitely** — When an API stalls mid-stream (no chunks arriving), Anteroom now detects the stall and times out gracefully instead of spinning forever (#191)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.8)

### v1.14.7

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.7)


---

## February 19, 2026

### v1.14.6

**Fixed:**

- API connection and authentication errors now show clear, actionable messages instead of raw Python tracebacks or generic "internal error" text (#121)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.6)

### v1.14.5

**Fixed:**

- **Fixed stacking approval prompts in CLI.** When multiple MCP tools needed approval at the same time, prompts would stack on top of each other and spam "terminal" output. Now serialized with proper queueing (#187)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.5)

### v1.14.4

**New:**

- **ESC cancel hint on CLI thinking line**: When the AI is thinking for more than 3 seconds, a muted "esc to cancel" hint now appears on the thinking line. This makes the cancel shortcut discoverable without cluttering the UI (#185)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.4)

### v1.14.3

**Fixed:**

- **Embedding worker no longer retries unembeddable messages forever.** Previously, short messages (< 10 characters), messages that returned no embedding, and messages that repeatedly failed to store would be retried indefinitely. Now marked as skipped after detection (#183)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.3)

### v1.14.2

**Fixed:**

- Fixed Ctrl+C causing unhandled `ExceptionGroup` errors during MCP server shutdown (#174). The MCP SDK uses `anyio` TaskGroups internally, which raise `ExceptionGroup` on cancellation. Now caught and handled cleanly

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.2)

### v1.14.1

**Fixed:**

- **File uploads now accept common document formats** — Uploading Office documents (.docx, .xlsx, .pptx, .doc, .xls, .ppt), Markdown files, JSON, YAML, TOML, and other common formats no longer fails with a MIME type validation error (#176)
- **Markdown and text files upload correctly even when browsers send no MIME type** — When a browser sends `application/octet-stream` (no MIME type detected), the server now infers the type from the file extension (#176)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.1)

### v1.14.0

**New:**

- **Knowledge Sources**: A global knowledge store for your projects — upload files, save text notes, and bookmark URLs that persist across conversations. Sources can be tagged, grouped, and linked to projects (#180)
- Create text, URL, and file-based knowledge sources (#180)
- Full web UI for browsing, creating, editing, and deleting sources (#181)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.0)

### v1.13.0

**New:**

- **Local Embeddings — No API Key Required**: Anteroom now generates vector embeddings locally using [fastembed](https://github.com/qdrant/fastembed), an ONNX-based embedding library that runs entirely offline (#172)
- Default model: `BAAI/bge-small-en-v1.5` (384 dimensions, ~50MB download on first use)
- Install with: `pip install anteroom[embeddings]`

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.13.0)

### v1.12.3

**Fixed:**

- MCP server connection failures are now logged cleanly without a raw traceback. When an MCP server rejects a connection or fails the handshake, Anteroom presents a clear error message and continues starting up (#170)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.12.3)

### v1.12.2

**Fixed:**

- **Fixed CLI crash when reviewing codebases with special tokens**: The CLI would crash with a tiktoken error when message content contained special token patterns. Now handled gracefully (#168)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.12.2)

### v1.12.1

**Fixed:**

- **Narration cadence now actually works**: The narration cadence feature (introduced in v1.11.0) was not producing any output for modern models like GPT-4o that return empty content with tool calls. Fixed to inject narration prompts correctly (#169)
- Narration now fires reliably regardless of model behavior (#169)
- Default cadence unchanged: every 5 tool calls (`ai.narration_cadence: 5`)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.12.1)

### v1.12.0

**New:**

- **Configurable Tool Call Dedup**: When the AI makes many consecutive tool calls of the same type (e.g., editing 10 files in a row), they're now automatically collapsed into a summary line for cleaner output (#59)
- CLI: consecutive same-type tool calls collapse with a count summary (e.g., "... edited 5 files total") (#59)
- Web UI: consecutive same-type tool calls group into a collapsible `<details>` element with count (e.g., "edit_file × 5") (#59)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.12.0)


---

## February 18, 2026

### v1.11.0

**New:**

- **Progress Updates During Long Agentic Runs**: When the AI executes many tool calls in sequence (editing files, running tests, exploring code), it now gives periodic progress summaries so you know what's happening (#157)
- Configurable via `ai.narration_cadence` in config.yaml (default: every 5 tool calls) (#157)
- Set to `0` to disable and restore the previous silent behavior

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.11.0)

### v1.10.2

**Fixed:**

- **API timeout recovery**: After a timeout, the next request no longer hangs indefinitely. Previously, a timeout would leave the httpx connection pool in a broken state. Now properly cleaned up (#155)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.10.2)

### v1.10.0

**New:**

- **Claude Code-Quality System Instructions**: The default system prompt for `aroom chat` has been completely rewritten to match the quality and structure of professional agentic coding assistants (#153)
- **Tool preference hierarchy**: The AI now strongly prefers dedicated tools (read_file, edit_file, grep, glob_files) over bash for file operations, reducing errors and improving reliability (#153)
- **Code modification guidelines**: Instructions to read before editing, match codebase conventions, avoid over-engineering, and produce working code — not prototypes (#153)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.10.0)

### v1.9.4

**Fixed:**

- **Tool call notifications no longer disappear mid-session.** In multi-iteration agent loops (where the AI calls tools, thinks, then calls more tools), tool call panels in the Web UI were being cleared between iterations. Now preserved across the full response (#151)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.4)

### v1.9.3

**Fixed:**

- **Embedding worker no longer retries forever** when the embedding API returns a permanent error (e.g., model not found, invalid credentials). Previously, the worker would retry with exponential backoff indefinitely (#149)
- **Permanent errors** (404 model not found, 422 unprocessable, failed auth) immediately disable the worker with a clear log message
- **Transient errors** (429 rate limit, 503 server error, timeouts) trigger exponential backoff: 30s → 60s → 120s → up to 300s

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.3)

### v1.9.2

**Fixed:**

- Fixed CLI completion menu using white-on-black colors that clashed with the dark terminal theme — now uses the dark palette (gold highlight, chrome text on dark background) (#147)
- Added above-cursor positioning attempt for the completion menu to reduce clipping when the prompt is near the terminal bottom (best-effort — full fix coming in a future release) (#147)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.2)

### v1.9.1

**Improved:**

- Optimized `/code-review` and `/submit-pr` Claude Code skills to eliminate redundant agent work during deploy cycles, reducing token usage by ~170k per deploy (#145)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.1)

### v1.9.0

**New:**

- **Sub-Agent Loading Indicator**: When a sub-agent is running in the Web UI, you now see a distinctive loading state instead of the generic tool call panel (#143)
- A pulsing accent border that animates while the sub-agent works
- A prompt preview showing what the sub-agent is doing

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.0)

### v1.8.0

**New:**

- **MCP Tools in Sub-Agents**: Sub-agents spawned via `run_agent` can now access MCP (Model Context Protocol) tools from connected servers. Previously, child agents only had access to built-in tools (#100)
- MCP tool definitions are merged into the child agent's tool list (#100)
- Child agents can call MCP tools through real MCP servers (e.g., time, filesystem, databases)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.8.0)

### v1.7.0

**Fixed:**

- **CLI text readability on dark terminals**: Rich's `[dim]` style (SGR 2 faint) was nearly invisible on most dark terminal themes, making tool results, approval prompts, and metadata unreadable (#140)
- Replaced all `[dim]` and `grey62` markup with a defined color palette that meets WCAG AA contrast ratios (#140)
- Four named constants: `GOLD` (accents), `SLATE` (labels), `MUTED` (secondary text), `CHROME` (UI chrome)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.7.0)

### v1.6.0

**New:**

- **Sub-Agent Orchestration**: The AI can now spawn parallel child agents using the `run_agent` tool to break complex tasks into independent subtasks. Each sub-agent runs in its own conversation context (#95)
- Sub-agents execute in parallel with concurrency control via `asyncio.Semaphore` (#95)
- Configurable limits: max concurrent (5), max total (10), max depth (3), max iterations (15), wall-clock timeout (120s)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.6.0)

### v1.5.1

**Improved:**

- Remove stale test count tracking from CLAUDE.md and skill definitions — the hardcoded count went stale constantly and skills wasted cycles checking/updating it (#138)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.5.1)

### v1.5.0

**New:**

- **Issue Lifecycle Management**: Three new Claude Code skills for managing the issue → branch → PR → deploy lifecycle, plus seven GitHub labels for tracking priority and status (#136)
- `/next` — Prioritized work queue sorted by priority labels and VISION.md direction areas. Shows what to work on next with rationale (#136)
- `/triage` — Set priority on individual issues or AI-reassess all open issues against VISION.md. Optionally updates ROADMAP.md (#136)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.5.0)

### v1.4.11

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.11)

### v1.4.10

**Fixed:**

- **Stale Auth Cookie Recovery on Upgrade**: Users upgrading from pre-identity versions (before v1.4.5) could get stuck in an authentication loop where the browser had an outdated session cookie (#128)
- Server now attaches a fresh session cookie to 401 responses, so browsers auto-recover without a manual page refresh (#128)
- Partial identity configs (user_id present but missing private_key) are now auto-repaired on server startup (#128)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.10)

### v1.4.9

**New:**

- **CLI Startup Progress Feedback**: The CLI no longer sits silently during bootstrap. Dim animated spinners now show activity during the three slow startup phases (#122):
- MCP server connections (#122)
- AI service validation (#122)

**Fixed:**

- Fixed compatibility with newer OpenAI models (e.g., gpt-5.2) that reject the deprecated `max_tokens` parameter in `aroom --test`. Now uses `max_completion_tokens` (#122)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.9)

### v1.4.8

**Fixed:**

- Fixed the web UI "stuck thinking" animation that would never dismiss after a response completed. The thinking indicator was being created multiple times but only one instance was tracked for dismissal (#128)
- Fixed 401 authentication errors for users upgrading from pre-identity versions. The chat stream now properly handles expired sessions instead of showing an opaque error (#128)
- Fixed SSE EventSource reconnect loop — persistent auth failures (3+ consecutive) now trigger session recovery instead of reconnecting indefinitely. (#128)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.8)

### v1.4.7

**Fixed:**

- **CI: Snyk security scan now passes green** — the Snyk SCA scan was crashing due to a dependency resolver bug in the Snyk Docker container (not an actual vulnerability in Anteroom). Switched to a workaround configuration (#126)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.7)

### v1.4.6

**Fixed:**

- Fixed Windows mapped network drive paths resolving to blocked UNC paths. On Windows, accessing files on mapped drives (e.g., `X:\test` where `X:` maps to a network share) no longer triggers a false path-traversal block (#124)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.6)

### v1.4.5

**Improved:**

- Hardened deploy workflow to handle recurring merge failures — auto-rebases, waits for CI, uses `--admin` only when non-required checks fail (#120)
- Fixed pre-push hook blocking version bump pushes with `--no-verify` (#120)
- Fixed zsh glob expansion error on `*.egg-info` cleanup (#120)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.5)

### v1.4.4

**Fixed:**

- Fixed Rich markup injection in approval output — tool names containing brackets or colons (common with MCP tools) are now properly escaped (#111)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.4)

### v1.4.3

**Fixed:**

- **UI hangs after MCP tool approval (#110)**: After approving MCP tool calls, the web UI could become completely unresponsive. This release fixes five interrelated issues (#110)
- **Stale stream detection** — when a browser tab disconnects or times out, the server now detects the stale SSE stream and cleans it up instead of blocking new requests (#110)
- **Thinking spinner stuck forever** — the "thinking" animation now correctly dismisses on all completion paths including errors and canvas operations (#110)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.3)


---

## February 17, 2026

### v1.4.2

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.2)

### v1.4.1

**Fixed:**

- **MCP tool approval flow no longer stalls after clicking Allow.** Previously, after approving an MCP tool in the web UI, there was no visual feedback that the tool was executing. Now shows a progress indicator immediately (#108)
- **CLI approval prompt now accepts keyboard input.** The tool approval prompt in the CLI REPL was unresponsive — you couldn't type y/n/a/s. Fixed by integrating with prompt_toolkit's input handling (#108)

**Improved:**

- CLI banner now correctly shows **ANTEROOM** instead of the old project name, with the updated tagline "the secure AI gateway."

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.1)

### v1.4.0

**New:**

- **Tool Approval System**: A Claude Code-style safety gate for AI tool execution. Every tool is assigned a risk tier (read, write, execute, destructive), and the approval mode determines which tiers require user confirmation (#106)
- **4 risk tiers**: read (safe), write (modifies files), execute (runs code), destructive (irreversible)
- **4 approval modes**: `auto` (no prompts), `ask_for_dangerous` (destructive only), `ask_for_writes` (default — write+execute+destructive), `ask` (alias)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.0)

### v1.3.1

**Fixed:**

- **New Chat button broken** — Clicking "New Chat" in the Web UI failed with a 415 error when no project was selected. The Content-Type header was only sent for project-scoped requests (#104)
- **CSP inline script blocked** — The Content Security Policy hash for the theme initialization script was stale, causing the browser to block it. Updated to match the current script content (#104)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.3.1)

### v1.3.0

**New:**

- **Canvas Tools with Real-Time Streaming**: Anteroom now includes a canvas panel for AI-generated content alongside chat. When the AI writes code, documents, or structured content, it streams into a side panel in real time (#89)
- **Create canvas** — AI can open a canvas panel with any content (#89)
- **Update canvas** — Full content replacement for major revisions (#89)

**Improved:**

- Note and document conversation types for non-chat content (#89)
- Canvas CRUD API endpoints for programmatic access (#89)
- Product vision document (VISION.md) establishing project guardrails (#88)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.3.0)


---

## February 16, 2026

### v1.2.0

**New:**

- **Semantic Search**: Anteroom now supports vector similarity search across your conversation history, powered by sqlite-vec. Search finds semantically related messages even when exact keywords don't match (#82)
- Semantic search API endpoints: `/api/search/semantic` and `/api/search/hybrid` (#82)
- Background embedding worker processes messages automatically using any OpenAI-compatible embedding API (#82)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.2.0)

### v1.1.0

**New:**

- **Cryptographic Identity (#68)**: Every Anteroom user now gets a unique cryptographic identity — a UUID paired with an Ed25519 keypair. This is the foundation for future features like message signing and multi-user attribution (#68)
- UUID + Ed25519 keypair generated automatically on first run or via `aroom init` (#68)
- Private key stored securely in `config.yaml` (file permissions set to 0600) (#68)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.1.0)


---

## February 15, 2026

### v0.9.1

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.9.1)

### v0.9.0

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.9.0)

### v0.8.3

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.8.3)

### v0.8.2

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.8.2)

### v0.8.1

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.8.1)

### v0.8.0

**New:**

- **Verbosity system**: Three display modes for tool calls — compact (default), detailed, and verbose
- **`/detail` command**: Replay last turn's tool calls with full arguments and output on demand
- **Live tool spinners**: Each tool call shows an animated spinner while executing

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.8.0)

### v0.7.2

**Fixed:**

- Connection failures now show descriptive error context instead of generic messages

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.7.2)

### v0.7.1

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.7.1)


---

## February 14, 2026

### v0.7.0

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.7.0)

### v0.6.9

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.6.9)

### v0.6.8

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.6.8)

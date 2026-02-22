# Feature Parity: CLI and Web UI

> Vision principle: **"Two interfaces, one engine."** The web UI and CLI share the same agent loop, storage, and tools. Features work in both interfaces or have a clear reason why they don't. The CLI is not a second-class citizen.

## The Rule

When adding or modifying a feature, the behavior MUST be equivalent in both the CLI and the web UI. The interfaces may look different — Rich TUI panels vs HTML/JS components — but the underlying behavior, capabilities, and data flow must match.

## What Parity Means

- **Same capabilities**: if the CLI can do it, the web UI can do it, and vice versa
- **Same data flow**: both interfaces read/write the same DB tables, use the same storage functions, go through the same agent loop
- **Same safety guarantees**: approval gates, tool tiers, and security checks apply identically
- **Different presentation is fine**: a Rich panel in the CLI and an HTML card in the web UI are parity. A feature that only exists in one interface is not.

## Examples

| Feature | CLI | Web UI | Parity? |
|---------|-----|--------|---------|
| Planning mode | `/plan on` command | `plan_mode` in chat request body | Yes — same behavior, different trigger |
| Canvas | Rich panel in terminal | HTML panel in sidebar | Yes — same data, different rendering |
| Approval gates | Interactive y/N prompt | SSE notification + approval endpoint | Yes — same safety, different UX |
| Conversation resume | `--continue` / `--conversation-id` flags | Click conversation in sidebar | Yes — same storage query |
| CLI keybindings | Escape to cancel | N/A | Exception OK — terminal-specific |
| Web UI CSS theme | N/A | Dark mode toggle | Exception OK — browser-specific |

## When Creating Issues (`/new-issue`)

Acceptance criteria MUST include both interfaces unless an exception applies:

- `[ ] Works in CLI: <specific behavior>`
- `[ ] Works in web UI: <specific behavior>`
- `[ ] Behavior is equivalent across both interfaces`

If the feature only applies to one interface, the issue MUST state why:

> **Parity exception**: This feature is CLI-only because it involves terminal-specific keybinding handling. No web UI equivalent is needed.

## When Submitting PRs (`/submit-pr`)

Before submitting, verify:

1. If the PR adds a feature to one interface, does the other interface have it too?
2. If the PR modifies shared code (agent loop, storage, tools), are both interfaces tested?
3. If parity is deferred, is there a follow-up issue tracking the other interface?

## What Doesn't Need Parity

- Terminal-specific mechanics (keybindings, ANSI escape codes, prompt_toolkit integration)
- Browser-specific mechanics (CSS, DOM manipulation, SSE client)
- Interface-specific UX polish (Rich spinners, HTML animations)
- Development tooling (CLI-only `--test` flag for connection validation)

## When Parity Is Missing

If you discover an existing feature that lacks parity:

1. Don't block the current work
2. Create a follow-up issue: `/new-issue <feature> is missing CLI/web UI parity`
3. Label it `enhancement`
4. Reference this rule in the issue description

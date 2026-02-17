# Anteroom — Product Vision

## What Anteroom Is

Anteroom is a lean, secure, self-hosted AI interface that connects to any OpenAI-compatible API. It gives teams inside locked-down enterprise environments the power of agentic AI — without sending data to third parties, without Docker sprawl, without compromising on security.

**Install it with pip. Run it in seconds. Own your data completely.**

Think of it as distributed AI for the enterprise — like BitTorrent, but for AI collaboration. Each instance is autonomous, lightweight, and runs wherever your team needs it. No central server. No cloud dependency. Just `pip install anteroom` and go.

## Who It's For

### Primary: Enterprise teams behind the firewall
Organizations that have access to LLMs (local or self-hosted OpenAI-compatible APIs) but can't use Claude Code, ChatGPT, or other cloud AI tools due to security policies. Anteroom gives them a private, auditable, fully controlled AI interface.

### Secondary: Collaborative development teams
Small and large teams who want to:
- Share and collaborate on AI conversations
- Work together on coding problems with agentic tools
- Integrate AI into their development workflow (editor extensions, project tools)

### Tertiary: Privacy-conscious power users
Individual developers and technical users who want full control over their AI stack — local data, configurable tools, CLI-first workflow.

## Core Principles

These principles guide every feature decision. New features must align with at least one and violate none.

### 1. Zero-friction setup
`pip install anteroom && aroom` — that's it. No Docker, no database server, no config files required to get started. SQLite, not Postgres. Sane defaults, not setup wizards. If a feature requires external infrastructure to work, it must degrade gracefully without it.

### 2. Security is structural, not optional
OWASP ASVS Level 2. HttpOnly cookies, CSRF protection, parameterized queries, path traversal prevention — these aren't add-ons, they're the foundation. Every feature ships secure by default. Enterprise security teams should be able to audit the codebase and find nothing to object to.

### 3. Lean over sprawling
Do fewer things well. Every feature must earn its place. Anteroom is highly configurable for power users — shareable configs, per-project settings, global defaults — but every option has a sensible default. A fresh install works out of the box. Complexity in the codebase is a bug; configurability for the user is a feature. The distinction: internal complexity bad, user control good.

### 4. Two interfaces, one engine
The web UI and CLI share the same agent loop, storage, and tools. Features work in both interfaces or have a clear reason why they don't. The CLI is not a second-class citizen — for many users, it's the primary interface.

### 5. Local-first, always
Data lives on the user's machine in SQLite. No phone-home, no telemetry, no cloud sync. The app works fully offline (given a local LLM). Network access is only for reaching the configured AI API.

### 6. Extensible through standards
MCP for tool integration. OpenAI-compatible API for model access. Standard protocols over proprietary plugins. Users extend Anteroom by connecting standard tools, not by writing Anteroom-specific code.

### 7. Collaborative without complexity
Multi-user and team features should feel as simple as the single-user experience. Collaboration means sharing conversations and working together — not user management, permissions matrices, or admin dashboards.

## What's In Scope

- **Web UI and CLI REPL** with shared agentic capabilities
- **Built-in tools**: file operations, bash, search, canvas/rich content
- **MCP integration** for extensible tool ecosystems
- **Knowledge management**: notebooks, documents, semantic search, RAG
- **Developer workflow**: editor extensions (VS Code), project management integration
- **Team collaboration**: shared conversations, collaborative coding
- **Autonomous agent features**: approval gates, long-running tasks, parallel tool execution

## What Anteroom Is Not

These aren't just out-of-scope items — they're identity statements. Anteroom's value comes as much from what it refuses to be as from what it does.

### Not a walled garden
Anteroom is extensible through MCP and standard protocols — but extensibility should feel like connecting tools, not building on a platform. A plugin store, shared skill library, or curated tool gallery may eventually make sense, but they must stay lightweight and optional. The moment extensibility requires its own infrastructure, admin UI, or review process, it's gone too far.

### Not a ChatGPT clone
Anteroom happens to have a chat interface, but the goal isn't to replicate ChatGPT. The chat is the interaction layer — the value is in the agentic tools, knowledge management, and developer workflow underneath. A feature that makes Anteroom "more like ChatGPT" without serving the core use cases is not a feature.

### Not a configuration burden
Anteroom should be highly configurable for those who want it — shareable configs, per-project settings, global defaults — but zero configuration should always work. Every option needs a sensible default. A fresh install with no config file should be fully functional. Configuration is power for the user, not a requirement to get started. If a feature doesn't work without configuration, the defaults are wrong.

### Not an enterprise product
Anteroom serves enterprise users, but it's not enterprise software. No license keys, no seat management, no SSO integration (unless trivially simple), no compliance dashboards. Enterprise teams use it because it's secure and self-hosted, not because it has enterprise features.

### Not a deployment project
If getting Anteroom running takes more than 2 minutes, something is broken. It's not Kubernetes-native. It doesn't need Helm charts. It doesn't have a "production deployment guide" that spans 10 pages. `pip install anteroom && aroom` — if that stops working, everything else is irrelevant.

### Not a model host
Anteroom talks to models. It doesn't run them, serve them, quantize them, benchmark them, or compare them. Model management is someone else's job. Anteroom connects to whatever's at the configured API endpoint and works with it.

## What's Out of Scope

These are explicit "no" decisions. Do not build features in these areas.

- **Cloud hosting / SaaS**: Anteroom is always self-hosted. No managed version, no hosted tier.
- **Model training or fine-tuning**: Anteroom consumes AI APIs. It does not train, fine-tune, or host models.
- **Mobile native apps**: Browser and terminal only. Responsive web is fine; native iOS/Android is not.
- **Complex deployment requirements**: If it needs Docker, Kubernetes, or a separate database server to run, it doesn't belong in core. Optional integrations (like Postgres) are acceptable as extras.
- **Admin dashboards / user management**: Multi-user should be lightweight. If it needs an admin panel, the design is too complex.
- **Competing with IDEs**: Anteroom is a companion, not a replacement for VS Code or JetBrains. Editor extensions connect to Anteroom; they don't recreate editor functionality.

## Direction (Current)

The project is heading toward four areas simultaneously:

1. **Knowledge management** — Notebooks, documents, semantic search, RAG. Making Anteroom a second brain that remembers and retrieves context.

2. **Deeper agentic capabilities** — More tools, autonomous workflows, approval gates, long-running tasks. Letting the AI do more with less hand-holding.

3. **Extensibility** — MCP ecosystem, custom tool authoring, shareable skills. Making it easy for teams to build on top of Anteroom.

4. **Developer workflow** — VS Code extension, Git integration, project management tools. Making Anteroom a natural part of how teams build software.

## The Litmus Test

Before adding a feature, ask:

1. **Can someone in a locked-down enterprise use this?** If it requires cloud access or third-party services that enterprises block, it's not core.
2. **Does it work with `pip install`?** If it adds heavy dependencies or external infrastructure requirements, reconsider.
3. **Is it lean?** If Open WebUI already does this with 500 lines of config, can we do it with 50? If not, is it worth the complexity?
4. **Does it work in both interfaces?** Web and CLI should stay in parity. If a feature only makes sense in one, that's fine — but justify it.
5. **Would we use it?** If the team wouldn't use this feature daily, it probably shouldn't exist.

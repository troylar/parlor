# Anteroom — Product Vision

## What Anteroom Is

Anteroom is a self-hosted AI gateway that connects to any OpenAI-compatible API. It gives entire organizations inside locked-down enterprise environments the power of agentic AI — not just chat, but an AI that can actually *do things*: edit files, generate documents, run commands, search codebases, create presentations — without sending data to third parties, without Docker sprawl, without compromising on security.

**Install it with pip. Run it in seconds. Own your data completely.**

An *anteroom* is the private chamber just outside a larger hall — a controlled space where you decide who enters and what leaves. Anteroom sits between your organization and AI: every prompt, every response, every tool call flows through a governed, auditable, self-hosted gateway that your security team controls.

Think of it as the open-source alternative to what JPMorgan built for 250,000 employees and Goldman Sachs built for 46,500 — available to every regulated institution that can't afford a nine-figure custom build. Each instance is autonomous, lightweight, and runs wherever your team needs it. No central server. No cloud dependency. Just `pip install anteroom` and go.

## Who It's For

### Primary: Regulated enterprises behind the firewall
Organizations that have access to LLMs (Azure OpenAI, Ollama, or self-hosted OpenAI-compatible APIs) but can't use ChatGPT, Claude Code, Cursor, or other cloud AI tools due to security and compliance policies. Anteroom gives them a private, auditable, fully controlled AI gateway for the entire workforce.

This includes every role that touches AI:
- **Developers and engineering teams** who want agentic AI coding without compliance friction — the CLI gives them a power tool that rivals Claude Code
- **Product owners and managers** who want AI that generates documents, analyzes data, and handles real tasks — not just answers questions — through the web UI
- **Executives and knowledge workers** who need AI assistance for strategic analysis, presentations, and document review in environments where every tool must pass security review
- **Marketing and communications teams** who use AI for content drafting, campaign analysis, and market research — within guardrails that prevent brand or compliance violations
- **Compliance and risk teams** who need to both *approve* AI tools (requiring audit trails, DLP, and controlled execution) and *use* AI tools (for regulatory research, policy analysis, examination prep)
- **Security teams (CISOs)** who need data sovereignty, prompt injection defense, and tamper-evident audit trails

### Secondary: Collaborative teams across functions
Teams who want to:
- Share and collaborate on AI conversations across departments
- Build and share reusable AI capabilities via Packs (a "compliance pack," a "proposal generation pack," a "DevOps pack," a "data analysis pack")
- Give non-technical team members access to powerful AI tools through the web UI while maintaining centralized security governance

### Tertiary: Privacy-conscious power users
Individual developers and technical users who want full control over their AI stack — local data, configurable tools, CLI-first workflow, no telemetry.

## The Problem Anteroom Solves

Every organization faces the same tension: employees want AI, compliance says no.

The result is shadow AI. 38% of employees admit to pasting confidential data into unauthorized AI tools. Marketing and sales teams use shadow AI at higher rates than engineering. Executives have the highest levels of *regular* unauthorized AI use. Shadow AI breaches cost organizations $650K+ per incident on average.

Cloud-hosted alternatives (ChatGPT Enterprise, Microsoft Copilot, Cursor) send data to third parties — a non-starter for regulated institutions. Self-hosted chat UIs (Open WebUI, LibreChat) lack agentic capabilities and security controls. Building a custom platform costs tens of millions and takes years.

Anteroom threads the needle: a self-hosted AI gateway with a polished web UI for every employee, an agentic CLI for developers, enterprise security controls, and tamper-evident audit trails — deployed with `pip install`.

## Core Principles

These principles guide every feature decision. New features must align with at least one and violate none.

### 1. Zero-friction setup
`pip install anteroom && aroom` — that's it. No Docker, no database server, no config files required to get started. SQLite, not Postgres. Sane defaults, not setup wizards. If a feature requires external infrastructure to work, it must degrade gracefully without it.

### 2. Security is structural, not optional
OWASP ASVS Level 2. HttpOnly cookies, CSRF protection, parameterized queries, path traversal prevention — these aren't add-ons, they're the foundation. Every feature ships secure by default. Enterprise security teams should be able to audit the codebase and find nothing to object to. Regulators should find tamper-evident audit trails for every AI interaction across the organization.

### 3. Lean over sprawling, but maximally configurable
Do fewer things well. Every feature must earn its place. But every behavior that can vary — timeouts, thresholds, limits, safety gates, approval modes, token budgets, retry policies, tool permissions — must be exposed as a configuration knob with a sensible default. Enterprise security teams need to lock down, tune, and audit every aspect of the system without touching code. Zero configuration must always work out of the box; but a team that needs to enforce token budgets, restrict tool access, cap sub-agent depth, or allowlist egress domains should be able to do all of that through config alone. Shareable configs, per-project settings, team-enforced fields, global defaults. Internal complexity is a bug; user-facing configurability is a feature.

### 4. Multiple interfaces, one engine
The web UI and CLI share the same agent loop, storage, and tools. Features work in both interfaces or have a clear reason why they don't. The web UI is the primary interface for most users — product owners, executives, compliance officers, marketing teams. The CLI is the power tool for developers who want agentic capabilities. Both are first-class citizens of the same governed platform.

### 5. Local-first, always
Data lives on the user's machine in SQLite. No phone-home, no telemetry, no cloud sync. The app works fully offline (given a local LLM). Network access is only for reaching the configured AI API.

### 6. Extensible through standards
MCP for tool integration. OpenAI-compatible API for model access. Standard protocols over proprietary plugins. Users extend Anteroom by connecting standard tools, not by writing Anteroom-specific code.

### 7. Collaborative without complexity
Multi-user and team features should feel as simple as the single-user experience. Collaboration means sharing conversations and working together — not user management, permissions matrices, or admin dashboards.

## What's In Scope

- **Web UI and CLI REPL** with shared agentic capabilities — web UI for all users, CLI for developers
- **Built-in tools**: file operations, bash, search, canvas/rich content
- **MCP integration** for extensible tool ecosystems (Office documents, databases, APIs, internal services)
- **Knowledge management**: notebooks, documents, semantic search, RAG
- **Enterprise knowledge work**: document generation, data analysis, presentation creation, report building — via MCP tools and Packs
- **Developer workflow**: editor extensions (VS Code), project management integration
- **Team collaboration**: shared conversations, shared Packs, cross-functional AI capabilities
- **Autonomous agent features**: approval gates, long-running tasks, parallel tool execution
- **Governance and audit**: tamper-evident audit trails, token budgets, team config enforcement, DLP

## What Anteroom Is Not

These aren't just out-of-scope items — they're identity statements. Anteroom's value comes as much from what it refuses to be as from what it does.

### Not a walled garden
Anteroom is extensible through MCP and standard protocols — but extensibility should feel like connecting tools, not building on a platform. A plugin store, shared skill library, or curated tool gallery may eventually make sense, but they must stay lightweight and optional. The moment extensibility requires its own infrastructure, admin UI, or review process, it's gone too far.

### Not a ChatGPT clone
Anteroom happens to have a chat interface, but the goal isn't to replicate ChatGPT. The chat is the interaction layer — the value is in the agentic tools, knowledge management, governance, and enterprise workflow underneath. A feature that makes Anteroom "more like ChatGPT" without serving the core use cases is not a feature.

### Not just a coding tool
Claude Code, Cursor, Kilo Code, and Copilot are AI coding tools for developers. Anteroom is an AI gateway that *includes* coding capabilities but serves the entire organization — document generation, data analysis, presentations, compliance research, strategic analysis. Developers are the beachhead (they can `pip install`), not the boundary.

### Not a configuration burden — but a configuration powerhouse
Anteroom must offer a plethora of configuration knobs and levers for enterprise security and operational flexibility. Every behavioral parameter — safety gates, token limits, tool permissions, retry policies, approval workflows, egress controls — should be configurable. But zero configuration must always work. Every knob needs a sensible default. A fresh install with no config file should be fully functional and secure. The bar: enterprise teams can enforce any policy through config alone, while a solo developer never has to touch a config file. If a feature doesn't work without configuration, the defaults are wrong.

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

The project is heading toward five areas simultaneously:

1. **Governance and audit** — Tamper-evident audit trails, compliance-ready logging, token budgets, team config enforcement. Making Anteroom the AI gateway that CISOs and CCOs can approve for the entire organization.

2. **Enterprise knowledge work** — Document generation, presentation creation, data analysis, reporting. Making Anteroom useful to product owners, managers, executives, and compliance officers — not just developers. MCP tools for Office formats (Word, Excel, PowerPoint) are the starting point; Packs bundle these into shareable workflows.

3. **Extensibility** — MCP ecosystem, custom tool authoring, shareable Packs. Making it easy for teams to build and distribute department-specific AI capabilities — a "compliance pack," a "proposal pack," a "DevOps pack," a "data analysis pack."

4. **Knowledge management** — Notebooks, documents, semantic search, RAG. Making Anteroom a second brain that remembers and retrieves context across conversations and users.

5. **Developer workflow** — VS Code extension, Git integration, project management tools. Making Anteroom a natural part of how developers build software. Deeper agentic capabilities: autonomous workflows, approval gates, long-running tasks, sub-agent orchestration.

## The Litmus Test

Before adding a feature, ask:

1. **Can someone in a locked-down enterprise use this?** If it requires cloud access or third-party services that enterprises block, it's not core.
2. **Does it work with `pip install`?** If it adds heavy dependencies or external infrastructure requirements, reconsider.
3. **Is it lean?** If Open WebUI already does this with 500 lines of config, can we do it with 50? If not, is it worth the complexity?
4. **Does it work in both interfaces?** Web and CLI should stay in parity. If a feature only makes sense in one, that's fine — but justify it.
5. **Would a product owner use it?** If only a developer would use this feature, that's fine — but if it *could* serve a broader audience with minimal extra effort, design it that way.
6. **Would a CISO approve it?** If a feature weakens the security posture or creates audit gaps, it doesn't ship.

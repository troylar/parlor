# Anteroom — Product Vision

## What Anteroom Is

Anteroom is a self-managed AI gateway that connects to any OpenAI-compatible API. It gives entire organizations the power of agentic AI — not just chat, but an AI that can actually *do things*: edit files, generate documents, run commands, search codebases, create presentations — without sending data to third parties and without compromising on security.

**Full enterprise governance. Deployed in minutes, not months.**

An *anteroom* is the private chamber just outside a larger hall — a controlled space where you decide who enters and what leaves. Anteroom sits between your organization and AI: every prompt, every response, every tool call flows through a governed, auditable gateway that your security team controls.

Think of it as the open-source alternative to what JPMorgan built for 250,000 employees and Goldman Sachs built for 46,500 — available to every regulated institution that can't afford a nine-figure custom build.

Anteroom runs two ways:
- **Local**: A developer runs it on their laptop with `pip install anteroom`. SQLite, local files, full CLI + web UI. Great for individual use or trying it out.
- **Server**: An enterprise deploys it as a centralized server — on-prem, cloud VPC, or bare metal. Docker/K8s, Postgres backend, SSO, RBAC. Hundreds of users connect via browser. Same codebase, same security, same audit trail. This is how banks run it.

## Who It's For

### Primary: Regulated enterprises
Organizations that have access to LLMs (Azure OpenAI, Ollama, or self-hosted OpenAI-compatible APIs) but can't use ChatGPT, Claude Code, Cursor, or other cloud AI tools due to security and compliance policies. Whether deployed on-prem, in a cloud VPC, or on bare metal — Anteroom gives them a private, auditable, fully controlled AI gateway for the entire workforce.

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

Anteroom threads the needle: a self-managed AI gateway with a polished web UI for every employee, an agentic CLI for developers, enterprise security controls, and tamper-evident audit trails. A developer gets started with `pip install`. A bank deploys it as a centralized server with Docker, Postgres, and SSO.

The typical enterprise adoption path is bottom-up: a developer installs locally, proves value with their team, and the organization deploys a centralized server. Both modes are first-class — and the transition from local to server is a supported migration path, not a rip-and-replace.

## Why Not Just Use Azure OpenAI Directly?

This is the question every bank CTO will ask. Azure OpenAI gives you a compliant API endpoint — but an API endpoint is not a product your workforce can use. You still need:

- A **web UI** so product owners, compliance officers, and executives can use AI without writing code
- An **agentic runtime** so AI can edit files, run commands, generate documents — not just chat
- **Audit trails** that satisfy examiners (HMAC-chained, tamper-evident, SIEM-ready)
- **Governance controls** — tool approval gates, DLP, bash sandboxing, token budgets
- **Team configuration** — per-department settings, locked security policies, RBAC
- **A CLI** so developers get a power tool, not a dumbed-down chat window

Building all of that on top of Azure OpenAI is exactly the custom build that costs banks tens of millions. Anteroom is that layer — pre-built, open-source, and deployable in a day. It connects to Azure OpenAI (or Ollama, or any OpenAI-compatible endpoint) and provides the governed, agentic UI that the raw API doesn't.

## Core Principles

These principles guide every feature decision. New features must align with at least one and violate none.

### 1. Deploy in minutes, not months
`pip install anteroom && aroom` gets a developer running in 60 seconds. `docker run` gets a team running in 5 minutes. A full enterprise deployment with SSO, Postgres, and team config takes a day — not the 6-12 months a custom build requires. Every feature must work at all three scales: solo developer, team, and enterprise.

### 2. Security is structural, not optional
OWASP ASVS Level 2. HttpOnly cookies, CSRF protection, parameterized queries, path traversal prevention — these aren't add-ons, they're the foundation. Every feature ships secure by default. Enterprise security teams should be able to audit the codebase and find nothing to object to. Regulators should find tamper-evident audit trails for every AI interaction across the organization.

### 3. Maximum configurability
Every behavior that can vary must be exposed as a configuration knob. This is non-negotiable — it's one of Anteroom's core differentiators. Enterprise teams need to control *everything* without touching code:

- **Security**: approval modes, tool permissions, risk tiers, hard-block patterns, bash sandboxing (timeouts, path blocking, network restrictions, command blocking), prompt injection defenses, DLP rules, IP allowlisting, session limits, CORS origins
- **Cost governance**: token budgets (per-request, per-conversation, per-day, per-user), model selection, context window limits, max iterations per turn
- **Agent behavior**: sub-agent concurrency, depth limits, iteration caps, output limits, timeout policies, retry strategies, planning mode defaults
- **Team enforcement**: lock any config field org-wide so no individual can override it. Security settings, tool access, approval modes, token budgets — all lockable.
- **Precedence layers**: built-in → global → team → space → conversation → runtime. Six layers of config resolution so departments can customize without weakening security.

Zero configuration must always work — a fresh `pip install` with no config file should be fully functional and secure. But a team that needs 50 knobs tuned to their exact policy should find all 50 knobs waiting. If a behavior isn't configurable, that's a bug.

### 4. Multiple interfaces, one engine
The web UI and CLI share the same agent loop, storage, and tools. Features work in both interfaces or have a clear reason why they don't. The web UI is the primary interface for most users — product owners, executives, compliance officers, marketing teams. The CLI is the power tool for developers who want agentic capabilities. Both are first-class citizens of the same governed platform.

### 5. Self-managed, data-sovereign
Your data stays where you put it. No phone-home, no telemetry, no cloud sync. Nothing leaves your environment except what you explicitly send to your chosen LLM endpoint. Deploy on-prem, in a cloud VPC, or on bare metal — Anteroom doesn't care where it runs, only that *you* control it.

### 6. Extensible through standards
MCP for tool integration. OpenAI-compatible API for model access. Standard protocols over proprietary plugins. Users extend Anteroom by connecting standard tools, not by writing Anteroom-specific code. In server mode, MCP server configuration is admin-governed — extensibility doesn't mean uncontrolled tool surface area.

### 7. Full enterprise governance, zero enterprise overhead
SSO/SAML/OIDC, RBAC, admin dashboard, audit trails, DLP, token budgets, team config enforcement, org-wide security controls — everything a regulated institution needs. No professional services engagement. No 6-month implementation. No license keys. The admin dashboard is focused and lean — security controls, configuration, audit visibility, usage monitoring — not a sprawling management console. A sysadmin sets it up in a day and never fights with it again.

## What's In Scope

- **Web UI and CLI REPL** with shared agentic capabilities — web UI for all users, CLI for developers
- **Built-in tools**: file operations, bash, search, canvas/rich content
- **MCP integration** for extensible tool ecosystems (Office documents, databases, APIs, internal services)
- **Knowledge management**: notebooks, documents, semantic search, RAG
- **Enterprise knowledge work**: document generation, data analysis, presentation creation, report building — via MCP tools and Packs
- **Developer workflow**: editor extensions (VS Code), project management integration
- **Team collaboration**: shared conversations, shared Packs, cross-functional AI capabilities
- **Autonomous agent features**: approval gates, long-running tasks, parallel tool execution
- **Governance and audit**: tamper-evident audit trails, token budgets, team config enforcement, DLP, per-user data isolation, MCP server governance
- **Admin dashboard**: centralized security controls, configuration management, audit log visibility, usage monitoring, access review exports
- **Enterprise identity**: SSO (SAML/OIDC), RBAC (per-role tool access, token budgets, approval modes), user provisioning (SCIM), session lifecycle management
- **Enterprise deployment**: Docker images, Kubernetes manifests, Postgres backend, health checks, log forwarding, graceful shutdown
- **Enterprise onboarding**: local-to-server migration path, DLP retroactive scanning, config transition transparency

## What Anteroom Is Not

These aren't just out-of-scope items — they're identity statements. Anteroom's value comes as much from what it refuses to be as from what it does.

### Not a ChatGPT clone
Anteroom happens to have a chat interface, but the goal isn't to replicate ChatGPT. The chat is the interaction layer — the value is in the agentic tools, knowledge management, governance, and enterprise workflow underneath. A feature that makes Anteroom "more like ChatGPT" without serving the core use cases is not a feature.

### Not just a coding tool
Claude Code, Cursor, Kilo Code, and Copilot are AI coding tools for developers. Anteroom is an AI gateway that *includes* coding capabilities but serves the entire organization — document generation, data analysis, presentations, compliance research, strategic analysis. Developers are the beachhead (they can `pip install`), not the boundary.

### Not a configuration burden — but a configuration powerhouse
Configurability is a first-class feature, not an afterthought. When a new feature ships, its configuration knobs ship with it. Every parameter has a sensible default so zero-config works, but the enterprise team that needs to lock down tool permissions, cap token budgets at $50/day, restrict bash to read-only paths, enforce a specific approval mode, and allowlist exactly three egress domains should be able to do all of that in a single YAML file. A solo developer never touches config. An enterprise security team controls everything through config. Both are first-class experiences.

### Not enterprise overhead
Anteroom has the enterprise features banks need — but without the enterprise overhead. Deployment complexity scales with deployment *size*, not with Anteroom itself. A solo developer never thinks about Docker. A bank gets an official image, health checks, K8s manifests, and SSO — and still deploys in a day, not a quarter. The measure isn't whether we have the feature — it's whether the feature deploys as easily as the rest of Anteroom.

### Not a model host
Anteroom talks to models. It doesn't run them, serve them, quantize them, benchmark them, or compare them. Model management is someone else's job. Anteroom connects to whatever's at the configured API endpoint and works with it.

## What's Out of Scope

These are explicit "no" decisions. Do not build features in these areas.

- **Managed SaaS (for now)**: Anteroom is currently self-managed. Deploy it on-prem, in your cloud VPC, or on bare metal. A hosted offering may make sense once the self-managed product is mature and there's clear demand — but it's not on the current roadmap.
- **Model training or fine-tuning**: Anteroom consumes AI APIs. It does not train, fine-tune, or host models.
- **Mobile native apps**: Browser and terminal only. Responsive web is fine; native iOS/Android is not.
- **Mandatory infrastructure dependencies**: Docker, Postgres, and K8s are supported but never required. Core Anteroom always runs with `pip install` and SQLite. Enterprise deployment options are additive, not mandatory.
- **Competing with IDEs**: Anteroom is a companion, not a replacement for VS Code or JetBrains. Editor extensions connect to Anteroom; they don't recreate editor functionality.

## Threat Model

Anteroom's security posture accounts for these adversary scenarios. Features that weaken these boundaries don't ship.

### Trust Boundaries

- **User ↔ Anteroom**: Authenticated via SSO (server mode) or local keypair (local mode). Sessions are HttpOnly, Secure, SameSite=Strict. CSRF double-submit protection on all state-changing endpoints.
- **Anteroom ↔ LLM API**: Egress-allowlisted. Only configured domains reachable. DLP scanning on both input (user prompts) and output (model responses). Prompt injection defense via canary tokens, context trust envelopes, and output filtering.
- **Anteroom ↔ MCP servers**: MCP servers are separate processes with their own network access. In server mode, MCP configuration is admin-only. MCP tool outputs are wrapped in untrusted content envelopes before injection into LLM context.
- **User ↔ User (server mode)**: Conversation isolation, RAG tenant isolation, per-user token budgets. Users cannot see each other's data unless explicitly shared via spaces.
- **Admin ↔ System**: Admin actions are audited. Config changes are logged. Session invalidation is immediate on role changes.

### Adversary Scenarios

| Scenario | Defense |
|---|---|
| **Malicious user exfiltrates data via MCP server** | MCP admin lockdown in server mode. Egress allowlisting. Audit trail on all tool calls. |
| **Prompt injection via RAG content** | Canary tokens, context trust XML envelopes, untrusted content wrapping, output content filtering. |
| **LLM generates dangerous tool calls** | 4-tier tool safety model, hard-block patterns, bash sandboxing, approval gates. |
| **Ex-employee retains access** | Session invalidation on deactivation. SCIM deprovisioning. Session timeout (12hr absolute, 30min idle). |
| **PII leaks to LLM endpoint** | DLP scanning (block by default in server mode), per-role DLP policies, audit logging of DLP events. |
| **Audit log tampered** | HMAC-SHA256 chain, append-only writes, automated integrity verification, Ed25519 signing. |
| **Token budget abuse (denial-of-wallet)** | Per-user daily/monthly budget caps, per-role limits, rate limiting per user. |

## Direction (Current)

The project is heading toward six areas. **Enterprise infrastructure is the critical path** — it unblocks multi-user deployment, which everything else depends on in server mode. Other directions progress in parallel where they don't depend on it.

1. **Enterprise infrastructure** *(critical path)* — Server mode deployment switch, Postgres backend, multi-user isolation, SSO (OIDC first, SAML second), RBAC, CLI device-flow auth, admin dashboard, Docker/K8s, health checks, log forwarding (Splunk/ELK), graceful shutdown. These are the table stakes that get Anteroom through a bank's architecture review. The build order is sequential: server mode switch → Postgres → multi-user → SSO → RBAC → admin dashboard → Docker/K8s.

2. **Governance and audit** — DLP hardening (block-by-default in server mode, industry-specific patterns), MCP server governance (admin-only in server mode), per-user data isolation (RAG tenant isolation), token budgets (per-user, per-role), audit enrichment (full identity on every event, automated chain verification, SIEM forwarding), regulatory retention compliance (minimum retention floors, access review exports). Making Anteroom the AI gateway that CISOs and CCOs can approve — and actively manage — for the entire organization.

3. **Enterprise knowledge work** — Document generation, presentation creation, data analysis, reporting. Making Anteroom useful to product owners, managers, executives, and compliance officers — not just developers. MCP tools for Office formats (Word, Excel, PowerPoint) are the starting point; Packs bundle these into shareable workflows.

4. **Extensibility** — MCP ecosystem (with admin governance in server mode), custom tool authoring, shareable Packs. Making it easy for teams to build and distribute department-specific AI capabilities — a "compliance pack," a "proposal pack," a "DevOps pack," a "data analysis pack."

5. **Knowledge management** — Notebooks, documents, semantic search, RAG (with tenant isolation in server mode). Making Anteroom a second brain that remembers and retrieves context across conversations and users.

6. **Developer workflow** — VS Code extension, Git integration, project management tools. Making Anteroom a natural part of how developers build software. Deeper agentic capabilities: autonomous workflows, approval gates, long-running tasks, sub-agent orchestration.

### Enterprise Onboarding Path

The typical adoption story at regulated institutions:

1. **Developer installs locally** — `pip install anteroom`, connects to internal LLM endpoint, proves value to team
2. **Team adopts** — `team.yaml` with enforce list, shared via git. DLP and audit enabled.
3. **Organization deploys server** — Docker/K8s, Postgres, SSO, RBAC. Developers transition from local to server mode.
4. **Local-to-server migration** — Conversations optionally migrated (with retroactive DLP scanning), config transitions transparently, CLI authenticates via device flow

This path is first-class. The migration from local to server is a supported workflow, not a gap.

## The Litmus Test

Before adding a feature, ask:

1. **Can someone in a locked-down enterprise use this?** If it requires cloud access or third-party services that enterprises block, it's not core.
2. **Does it work at all three scales?** Solo developer (`pip install`), team (`docker run`), enterprise (K8s + Postgres + SSO). Features should degrade gracefully, not break.
3. **Would it pass a bank's architecture review?** If the answer is "no, they'd need X first" — build X.
4. **Does it work in both interfaces?** Web and CLI should stay in parity. If a feature only makes sense in one, that's fine — but justify it.
5. **Would a product owner use it?** If only a developer would use this feature, that's fine — but if it *could* serve a broader audience with minimal extra effort, design it that way.
6. **Would a CISO approve it?** If a feature weakens the security posture or creates audit gaps, it doesn't ship.
7. **Is the enterprise feature as easy to deploy as the rest of Anteroom?** Enterprise governance is the differentiator — enterprise *overhead* is the thing we eliminate.

# Start Here

Pick the path that matches what you want to do. You don't need to understand packs, artifacts, or spaces to get started.

---

## I want to chat securely with an LLM

You have access to an OpenAI-compatible API (Azure OpenAI, OpenAI, Ollama, etc.) and want a private, self-hosted interface.

**Get started:**

1. [Install Anteroom](getting-started/installation.md) &mdash; one `pip install`, no Docker required
2. [Quickstart](getting-started/quickstart.md) &mdash; configure your endpoint and start chatting
3. [Web UI guide](web-ui/index.md) &mdash; conversations, themes, keyboard shortcuts, file attachments

**Web UI** is the primary interface for this path. Launch with `aroom`.

---

## I want an AI coding assistant

You're a developer and want an agentic AI that can read files, edit code, run commands, and search your codebase &mdash; like Claude Code or Cursor, but self-hosted.

**Get started:**

1. [Install Anteroom](getting-started/installation.md) &mdash; one `pip install`
2. [CLI guide](cli/index.md) &mdash; the REPL, built-in tools, and how the agent loop works
3. [Tools reference](cli/tools.md) &mdash; all 12 built-in tools and how to add MCP tools

**CLI** is the primary interface for this path. Launch with `aroom chat`.

---

## I want to share AI conventions across my team

You want everyone on your team to use the same coding standards, security rules, prompt templates, and model settings &mdash; without manually configuring each person's setup.

**Get started:**

1. [Install Anteroom](getting-started/installation.md) &mdash; each team member runs `pip install`
2. [Spaces quickstart](spaces/quickstart.md) &mdash; create a workspace for your project
3. [Packs quickstart](packs/quickstart.md) &mdash; install or create a pack with your team's rules and skills

**Both interfaces** work with packs and spaces. The CLI is better for authoring; the web UI is better for day-to-day use.

---

## What's next

Once you're comfortable, explore deeper:

- [Concepts](getting-started/concepts.md) &mdash; how the agent loop, tools, and config layers work
- [Configuration](configuration/index.md) &mdash; every knob Anteroom exposes
- [Security](security/index.md) &mdash; OWASP ASVS L2 compliance, audit logs, tool safety
- [Tutorials](tutorials/connect-to-ollama.md) &mdash; connect to specific providers, add MCP tools, build skills

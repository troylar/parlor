# Product Vision Alignment

Before creating issues, planning features, or starting work, check that the proposed work aligns with the product vision in `VISION.md`.

## Quick Reference — Core Principles

1. **Zero-friction setup** — pip install and go. No Docker, no external DB required.
2. **Security is structural** — OWASP ASVS Level 2. Secure by default, not by configuration.
3. **Lean over sprawling** — fewer features, done well. Complexity is a bug.
4. **Two interfaces, one engine** — web UI and CLI share the same core. Neither is second-class.
5. **Local-first, always** — SQLite, no cloud, no telemetry. Works offline with a local LLM.
6. **Extensible through standards** — MCP, OpenAI-compatible APIs. Standard protocols, not proprietary plugins.
7. **Collaborative without complexity** — team features should feel as simple as single-user.

## What Anteroom Is Not (Negative Guardrails)

These are identity-level constraints. If a feature makes Anteroom more like any of these, flag it.

- **Not a walled garden** — extensibility through MCP and standards, not proprietary ecosystems. A plugin store or shared skill library is fine if lightweight and optional, but extensibility should never require its own infrastructure or admin UI
- **Not a ChatGPT clone** — the chat is the interaction layer, not the product. Features that just make it "more like ChatGPT" without serving core use cases don't belong
- **Not a configuration burden** — highly configurable is good (shareable configs, per-project settings, global defaults), but zero configuration must always work. Every option needs a sensible default. If a feature doesn't work without configuration, the defaults are wrong
- **Not enterprise software** — no license keys, seat management, SSO, or compliance dashboards. It serves enterprise users by being secure and simple, not by having enterprise features
- **Not a deployment project** — if setup takes more than 2 minutes, something is broken
- **Not a model host** — Anteroom talks to models. It doesn't run, serve, quantize, or benchmark them

## Out of Scope (Hard No)

Do not build or propose features in these areas:
- Cloud hosting or SaaS
- Model training or fine-tuning
- Mobile native apps
- Complex deployment requirements (Docker-only, separate DB server)
- Admin dashboards or user management panels
- Recreating IDE functionality

## The Litmus Test

When evaluating a feature idea, ask:
1. Can someone in a locked-down enterprise use this?
2. Does it work with `pip install`?
3. Is it lean? Could we do this with less?
4. Does it work in both interfaces (or have a clear reason not to)?
5. Would the team use this daily?

If the answer to any of these is "no," flag the concern before proceeding. Read `VISION.md` for the full product vision.

## When Ideas Don't Align

If a user proposes work that conflicts with the vision:
- Don't silently proceed — raise the concern directly
- Explain which principle it conflicts with
- Suggest an alternative approach that aligns, if one exists
- If the user wants to proceed despite the concern: go ahead, but:
  1. Add the `vision-review` label to the issue/PR
  2. Note the specific vision tension in the issue/PR description
  3. This ensures the project owner can batch-review vision-flagged work

## When Ideas Are Ambiguous

If alignment isn't clear:
- Ask the user how the feature relates to the core use cases (enterprise behind firewall, collaborative teams, power users)
- Check if the feature adds external dependencies or infrastructure requirements
- Consider whether it increases or decreases the "pip install to productive" time

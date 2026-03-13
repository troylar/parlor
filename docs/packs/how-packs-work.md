# How Packs Work

This page is the deep dive. It explains the full lifecycle of a pack — from installation through to how it affects the running agent — covering config layering, conflict resolution, DB storage, rule enforcement, and skill resolution.

If you haven't read [Core Concepts](concepts.md) yet, start there for the mental model. This page explains the mechanics.

## The Pack Lifecycle

A pack goes through five phases before it affects the agent:

```
1. INSTALL      pack.yaml parsed, artifacts stored in SQLite
       |
2. ATTACH       pack linked to global or directory scope (with priority)
       |
3. LOAD         artifact registry reads DB, resolves precedence
       |
4. MERGE        config overlays merged into config chain by priority
       |
5. ENFORCE      rules checked at tool execution time, skills registered
```

Each phase is explained below.

---

## Phase 1: Install

Installing a pack parses the manifest and stores everything in SQLite.

```bash
aroom pack install ./my-pack/
```

What happens:

1. `pack.yaml` is parsed and validated (name, namespace, version, artifact list)
2. Each artifact file is read from disk (e.g., `skills/commit.yaml`, `rules/security.md`)
3. A `packs` row is created in the DB: `(id, namespace, name, version, source_path, ...)`
4. Each artifact gets an `artifacts` row: `(id, fqn, type, namespace, name, content, content_hash, source, metadata, version)`
5. A `pack_artifacts` junction row links each artifact to its pack

**After install, the pack exists in the DB but is not yet active.** Config overlays only apply when the pack is attached. Non-config artifacts (skills, rules, instructions, etc.) are also filtered by attachment state — `ArtifactRegistry.load_from_db()` passes `attached_only=True`, which excludes artifacts from unattached packs via a SQL JOIN on `pack_artifacts` and `pack_attachments`.

### Reinstall Behavior

If you install a pack that already exists (same namespace + name):

- The pack version and artifacts are **updated** (upsert, not duplicate)
- Existing pack attachments are **preserved** — you don't need to re-attach
- Artifact versions auto-increment if content changes (tracked by `content_hash`)

### What's Stored Where

| Data | Storage | Why |
|------|---------|-----|
| Artifact content (skills, rules, instructions, etc.) | SQLite `artifacts` table | Queryable, versioned, content-addressable |
| Pack metadata (name, namespace, version) | SQLite `packs` table | Enables list/show/remove without filesystem |
| Pack-artifact links | SQLite `pack_artifacts` junction table | Reference counting for shared artifacts |
| Attachment state | SQLite `pack_attachments` table | Tracks which packs are active and at what priority |
| Original pack files | Filesystem (source_path) or `.anteroom/packs/` (with `--project`) | For updates, the source is re-read |

The DB is the source of truth. Filesystem paths are only needed during install/update. Once installed, removing the source directory doesn't break anything — the content is in the DB.

---

## Phase 2: Attach

Attaching a pack makes it **active** for a given scope.

```bash
# Global — active everywhere
aroom pack attach acme/python-conventions

# Project — active only in the current working directory
aroom pack attach acme/python-conventions --project

# With priority (lower number = higher precedence)
aroom pack attach acme/security-baseline --priority 10
```

### Global vs Directory Scope

| Scope | Stored As | When Active |
|-------|-----------|-------------|
| **Global** | `pack_attachments` row with `project_path = NULL` | Always, in every session |
| **Directory** | `pack_attachments` row with `project_path = '/path/to/dir'` | Only when working directory is inside that path |

Directory-scoped attachments let you have different packs for different codebases. A security-focused pack for your banking app, a different coding-standards pack for your open-source project.

### Priority

Every attachment has a **priority** (integer 1-100, default 50). Priority controls what happens when multiple packs set the same config key:

```
1-19     high priority    (compliance, security baselines)
20-49    above normal
50       default
51-80    below normal
81-100   low priority     (fallback defaults, easily overridden)
```

**Lower number = higher precedence.** A pack attached at priority 10 overrides one at priority 50 for the same config key.

### Conflict Detection at Attach Time

When you attach a pack, Anteroom checks for conflicts with already-attached packs. The rules depend on the artifact type:

**Config overlays** — priority-based:

| Situation | Result |
|-----------|--------|
| Two packs set `safety.approval_mode`, different priorities | Allowed — lower priority number wins |
| Two packs set `safety.approval_mode`, same priority (50) | **Error** — you must change one pack's priority or detach it |

**Skills** — additive (namespace-aware):

| Situation | Result |
|-----------|--------|
| Two packs both define a skill named `deploy` | Allowed — both are active as `/ns-a/deploy` and `/ns-b/deploy`. Unique names keep their bare form |

**Rules, instructions, context, memory, MCP servers** — additive (all apply):

| Situation | Result |
|-----------|--------|
| Two packs both define a rule named `security` | Allowed — both rules are active. They add guidance, not conflict |

This means:
- You can stack multiple rule packs (security + coding standards + documentation)
- You can stack skill packs — colliding names are qualified with their namespace
- You can stack config overlays if they have different priorities or don't overlap

### Detach

Detaching removes the attachment but keeps the pack installed:

```bash
aroom pack detach acme/python-conventions
aroom pack detach acme/python-conventions --project  # directory scope only
```

The pack's artifacts become inactive but remain in the DB. Re-attach later without reinstalling.

---

## Phase 3: Load (Artifact Registry)

On startup (and after attach/detach/install), Anteroom loads artifacts from the DB into the in-memory `ArtifactRegistry`. Only artifacts from **attached** packs are included — unattached pack artifacts are excluded at query time.

```
SQLite (artifacts + pack_artifacts + pack_attachments)
       |  filtered by attached_only=True
       v
ArtifactRegistry.load_from_db(space_id=...)
       |
       v
In-memory index (precedence-resolved, searchable)
```

When a space is active, `load_from_db()` accepts a `space_id` parameter. This scopes the query to include artifacts from packs attached globally OR attached to that specific space. In the web UI, per-request registries are built via `_get_request_registries()` so each request sees the correct space-scoped artifacts.

The registry resolves the 6-layer precedence stack:

```
INLINE     (5)  ← highest priority
LOCAL      (4)  ← .anteroom/skills/ in current dir
PROJECT    (3)  ← installed packs, .anteroom/skills/ in project
TEAM       (2)  ← team config artifacts
GLOBAL     (1)  ← ~/.anteroom/skills/
BUILT_IN   (0)  ← ships with Anteroom
```

When two artifacts have the same FQN (`@namespace/type/name`), the higher-layer one wins. This means:

- A local skill file overrides a pack's skill of the same name
- A pack's skill overrides a built-in skill of the same name
- You can always override anything by placing a file in `.anteroom/skills/` (local layer)

### What the Registry Feeds

| Consumer | What It Gets | Space-Scoped? |
|----------|-------------|---------------|
| **SkillRegistry** | `skill` artifacts → slash commands and `invoke_skill` tool | Yes — per-request in web UI, per-session in CLI |
| **RuleEnforcer** | `rule` artifacts with `enforce: hard` → tool call blocking | Yes — passed as `rule_enforcer_override` (no shared state mutation) |
| **System prompt** | `rule`, `instruction`, `context` artifacts → injected text | Yes |
| **Config loader** | `config_overlay` artifacts → merged into config chain | Yes — attached-only, excluded from registry load |
| **MCP manager** | `mcp_server` artifacts (reserved type, not yet loaded at runtime — MCP servers are configured via `config.mcp_servers`) | N/A |

---

## Phase 4: Config Merge

Config overlays from attached packs are merged into the configuration precedence chain.

### The Full Precedence Chain

```
env vars / CLI flags     ← highest priority (always wins)
       |
project config           ← .anteroom/config.yaml in project dir
       |
space config             ← space YAML config section
       |
personal config          ← ~/.anteroom/config.yaml
       |
PACK OVERLAYS            ← merged from attached packs, sorted by priority
       |
team config              ← team config distribution
       |
defaults                 ← built-in defaults from config dataclasses
```

**Key insight:** Pack overlays sit between team config and personal config. This means:

- Teams can set baselines that packs cannot override (using `enforce`)
- Users can override pack settings in their personal config
- Project config and CLI flags override everything

### How Pack Overlays Merge

When multiple packs have config overlays, they merge in priority order (lower number first):

```
Pack A (priority 10): safety.approval_mode = "ask"
Pack B (priority 50): safety.approval_mode = "auto", cli.verbose = true
Pack C (priority 80): cli.verbose = false, ai.temperature = 0.7
```

Merge result:

```yaml
safety:
  approval_mode: ask       # Pack A wins (priority 10 < 50)
cli:
  verbose: true            # Pack B wins (priority 50 < 80)
ai:
  temperature: 0.7         # Pack C only setter, no conflict
```

The merged result is then deep-merged into the config chain at the "packs" layer.

### Team Enforcement

Team configs can **enforce** specific fields. Enforced fields cannot be overridden by any layer — not by packs, not by personal config, not even by project config:

```yaml title="team config"
ai:
  model: gpt-4o
enforce:
  - ai.model
```

With this, a pack overlay setting `ai.model: gpt-3.5-turbo` is silently ignored. Team enforcement is re-applied after every merge step.

### Viewing Config Sources

To see where each config value came from:

```bash
aroom config view --with-sources
```

This annotates each key with its origin layer (default, team, pack, personal, space, project, env var).

---

## Phase 5: Enforce

Once loaded, artifacts affect the agent in real time.

### Rule Enforcement (Hard Rules)

Rules with `enforce: hard` in their metadata are checked before **every tool call**. A matching rule blocks the tool regardless of approval mode or user override.

```yaml title="rules/no-force-push.md metadata"
# Rule content (injected into system prompt)
Never force push to shared branches.

# Metadata (in pack.yaml or artifact DB)
enforce: hard
reason: Force pushing destroys shared history
matches:
  - tool: bash
    pattern: "git\\s+push\\s+--force"
  - tool: bash
    pattern: "git\\s+push\\s+-f"
```

How it works:

1. The `RuleEnforcer` loads all `rule` artifacts with `enforce: hard`
2. Each rule has one or more `matches` — a tool name (or `*` for all tools) and a regex pattern
3. Before every tool call, the enforcer checks all rules against the tool name and arguments
4. If a pattern matches, the tool call is **blocked** with a `hard_denied` verdict
5. The block reason is shown to the user and the AI

**Matching targets by tool:**

| Tool | What the regex matches against |
|------|-------------------------------|
| `bash` | The `command` argument |
| `write_file`, `edit_file`, `read_file` | The `path` argument |
| `*` (wildcard) | All string arguments concatenated |
| Any other tool | All string argument values joined |

**Soft rules** (`enforce: soft` or no `enforce` field) are injected into the system prompt as guidance but do not block tool calls. They rely on the AI following instructions rather than hard enforcement.

**Safety guards:**

- Regex patterns longer than 500 characters are rejected (ReDoS prevention)
- Invalid regex patterns are skipped with a warning
- Rules with no valid match patterns are ignored

### Skill Resolution (Namespace-Aware)

When multiple packs define skills with the same name, Anteroom uses **namespace-qualified names** to disambiguate.

**No collision — bare name works:**

```
Pack: acme/dev-tools   → skill "commit"
Pack: acme/docs        → skill "summarize"

/commit      → works (unique name)
/summarize   → works (unique name)
```

**Collision — must qualify with namespace:**

```
Pack: team-alpha/ops   → skill "deploy"
Pack: team-beta/ops    → skill "deploy"

/deploy               → ambiguous, returns warning with options
/team-alpha/deploy    → works (qualified)
/team-beta/deploy     → works (qualified)
```

When a bare name is ambiguous, Anteroom logs a warning:

```
Ambiguous skill 'deploy' -- qualify with namespace: /team-alpha/deploy, /team-beta/deploy
```

The `invoke_skill` tool definition (used by the AI) includes only unambiguous names and qualified names in its enum, so the AI always picks a valid target.

---

## Multiple Packs: A Worked Example

Suppose you have three packs:

```
acme/security-baseline  (priority 10)
  - rule: no-force-push (enforce: hard)
  - rule: no-env-writes (enforce: hard)
  - config_overlay: safety (approval_mode: ask_for_writes)

acme/python-conventions (priority 50)
  - skill: commit
  - skill: review
  - rule: coding-standards (enforce: soft)
  - config_overlay: defaults (approval_mode: auto)

acme/docs-tools (priority 50)
  - skill: summarize
  - skill: proofread
  - instruction: writing-guide
```

After attaching all three:

**Config merge result:**

```yaml
safety:
  approval_mode: ask_for_writes  # security-baseline wins (priority 10 < 50)
```

The `auto` from python-conventions is overridden because security-baseline has higher priority (lower number).

**Active skills:**

```
/commit      → acme/python-conventions
/review      → acme/python-conventions
/summarize   → acme/docs-tools
/proofread   → acme/docs-tools
```

No skill name collisions, so all bare names work.

**Active rules:**

```
Hard rules (enforced at tool layer):
  - no-force-push: blocks "git push --force" in bash
  - no-env-writes: blocks writing to .env files

Soft rules (injected into system prompt):
  - coding-standards: guides code style
```

**Instructions:**

```
writing-guide: injected into system prompt every turn
```

Rules from all packs are additive — they stack. The security pack's hard rules block dangerous operations, the python pack's soft rules guide code style, and the docs pack's instruction provides writing context. No conflicts because rules and instructions are additive types.

---

## DB Storage Architecture

Everything installed goes into SQLite. Here's how the tables relate:

```
packs                    pack_attachments
  id (PK)                  id (PK)
  namespace                pack_id (FK → packs.id)
  name                     project_path (NULL = global)
  version                  priority (1-100)
  source_path              space_id (FK, optional)
  UNIQUE(namespace, name)  UNIQUE(pack_id, COALESCE(project_path, ''))

pack_artifacts           artifacts
  pack_id (FK)             id (PK)
  artifact_id (FK)         fqn (UNIQUE)
  CASCADE on delete        type, namespace, name
                           content, content_hash
                           source, metadata
                           version (auto-incremented)

                         artifact_versions
                           artifact_id (FK, CASCADE)
                           version, content_hash
                           created_at
```

**Key behaviors:**

- **Reference counting**: An artifact shared by two packs has two `pack_artifacts` rows. Removing one pack doesn't delete the artifact — only when all referencing packs are removed (orphan detection)
- **Orphan detection runs inside a transaction** to prevent TOCTOU race conditions with concurrent pack operations
- **Content-addressable**: `content_hash` (SHA-256) detects changes. Re-installing a pack with unchanged content skips the artifact update
- **Version history**: Each content change creates an `artifact_versions` row. `aroom artifact show` displays the full history
- **CASCADE deletes**: Removing a pack cascades to `pack_artifacts`. Removing an artifact cascades to `artifact_versions`

### What's NOT in the DB

| Data | Location | Why |
|------|----------|-----|
| Source pack files | Filesystem (`source_path` or `.anteroom/packs/`) | Needed for `pack update` to re-read files |
| Lock file | `.anteroom/anteroom.lock.yaml` | Version-controlled for team reproducibility |
| Local artifacts | `.anteroom/skills/`, `.anteroom/rules/` | Filesystem-first for quick editing |
| Built-in skills | `src/anteroom/cli/default_skills/` | Ship with the package, not user data |
| Git pack source cache | `~/.anteroom/cache/sources/{hash}/` | Cloned repos for background refresh |

---

## Reload Behavior

Packs are live-reloaded in several scenarios:

| Event | What Reloads |
|-------|-------------|
| `aroom pack install` / `update` | Artifact registry, skill registry |
| `aroom pack attach` / `detach` | Artifact registry, skill registry, rule enforcer, config overlays |
| `aroom pack refresh` (git sources) | Artifact registry, skill registry |
| REPL `/pack install` / `/pack attach` | Same as CLI, within the active session |
| Web UI pack API calls | Artifact registry, rule enforcer via `_reload_registries()` |

The reload path in the CLI REPL:

```python
# 1. Reload artifacts from DB
artifact_registry.load_from_db(db)

# 2. Reload rules
rule_enforcer.load_rules(artifact_registry.list_all(artifact_type=ArtifactType.RULE))

# 3. Reload skills
skill_registry.load()
skill_registry.load_from_artifacts(artifact_registry)
```

This is atomic — the registries swap their entire internal state at once, so there's no window where partial state is visible.

---

## Filesystem vs DB: When to Use Which

| Approach | When to Use | How |
|----------|-------------|-----|
| **DB (packs)** | Team distribution, versioned bundles, config overlays, hard rules | `aroom pack install`, git pack sources |
| **Filesystem (local)** | Quick personal overrides, prototyping, one-off skills | Drop files in `.anteroom/skills/` or `.anteroom/rules/` |
| **Both** | Override a pack's skill locally | Install the pack (DB), then create a same-named skill file (filesystem). The local file wins via precedence |

**Rules of thumb:**

- If it's shared with a team → pack it
- If it's just for you → filesystem
- If it needs config overlays or hard rule enforcement → pack it (filesystem artifacts don't support metadata)
- If you're iterating quickly → filesystem first, pack it when stable

---

## Troubleshooting

### "Ambiguous skill 'X'" warning

Two attached packs define a skill with the same name. Use the qualified form: `/namespace/skill-name`. Or detach one of the packs.

### Config overlay conflict error on attach

Two packs set the same config key at the same priority. Fix by changing one pack's priority:

```bash
aroom pack detach acme/pack-b
aroom pack attach acme/pack-b --priority 80
```

### Hard rule blocking a command you need

A hard-enforced rule is blocking a tool call. Options:

1. Detach the pack containing the rule: `aroom pack detach ns/pack-name`
2. Ask the pack maintainer to change the rule from `hard` to `soft`
3. The rule cannot be overridden by the user — this is by design for security

### Pack installed but skills not showing

The pack may not be attached. Install and attach are separate steps:

```bash
aroom pack install ./my-pack/
aroom pack attach myteam/my-pack    # don't forget this step
```

Or use `--attach` during install:

```bash
aroom pack install ./my-pack/ --attach
```

### Artifact not taking effect

Check the precedence stack. A higher-layer artifact with the same FQN may be shadowing it:

```bash
aroom artifact check
```

Look for `shadow` info messages. Local files (layer 4) always beat pack artifacts (layer 3).

## Next Steps

- [Core Concepts](concepts.md) — the mental model
- [Pack Commands](pack-commands.md) — CLI reference
- [Config Overlay](../spaces/config-overlay.md) — space-level config merging
- [Manage Conflicts](tutorials/manage-conflicts.md) — tutorial on resolving pack conflicts

# Tutorial: Manage Conflicts

Understand what happens when two packs define the same artifact, how precedence resolves it, and how to intentionally override with local artifacts.

## The Scenario

You have two packs installed:

1. `core/built-ins` — ships with Anteroom, includes a `commit` skill
2. `acme/standards` — your team's pack, also includes a `commit` skill

Both define `@.../skill/commit`. Which one wins?

## Understanding Precedence

Anteroom resolves conflicts using the [6-layer precedence stack](../concepts.md#the-6-layer-precedence-stack):

```
  INLINE     ← highest (5)
  LOCAL      ← (4)
  PROJECT    ← (3)
  TEAM       ← (2)
  GLOBAL     ← (1)
  BUILT_IN   ← lowest (0)
```

The `acme/standards` pack installs at **project** level (3), which is higher than **built_in** (0). So `@acme/skill/commit` wins over `@core/skill/commit`.

## Step 1: Detect Collisions

```bash
$ aroom artifact check
```

```
Artifacts: 15  Packs: 2  Size: 8.3 KB  Tokens: ~2,075

Issues:
  WARN   skill_collision  Skill "commit" defined in @core/skill/commit and @acme/skill/commit
                          Active: @acme/skill/commit (project)
  INFO   shadow           @acme/skill/commit (project) shadows @core/skill/commit (built_in)

Healthy: yes (0 errors, 2 warnings, 0 info)
```

The health check reports:

- **skill_collision** (WARN): two skills share the name `commit`
- **shadow** (INFO): the higher-precedence one is active

This is **healthy** — no errors. Skill collisions are warnings because they're often intentional.

## Step 2: Verify Which Is Active

```bash
$ aroom artifact show @acme/skill/commit
```

This is the version that runs when you type `/commit`. The built-in is shadowed.

## Step 3: Intentional Override with Local Artifact

If you want to override the team pack's `commit` skill with your own version, create a **local** artifact. Local (layer 4) beats project (layer 3).

Create a skill file in your project's skill directory:

```yaml title=".anteroom/skills/commit.yaml"
name: commit
description: My personal commit helper
prompt: |
  Create a commit message. Keep it under 50 characters for the subject line.
  Use imperative mood. No issue reference needed.

  {args}
```

This file doesn't need a pack — it's discovered directly by the skill registry at the `local` layer.

```bash
$ aroom artifact check
```

```
Issues:
  WARN   skill_collision  Skill "commit" defined in 3 sources
                          Active: @local/skill/commit (local)
  INFO   shadow           @local/skill/commit (local) shadows @acme/skill/commit (project)
  INFO   shadow           @acme/skill/commit (project) shadows @core/skill/commit (built_in)
```

Now your local version wins.

## Step 4: Config Overlay Conflicts

Config overlay conflicts are more serious. If two packs set the same config field to different values, that's an **ERROR**:

```
ERROR  config_conflict  Field "safety.approval_mode" set to "auto" by @dev/config_overlay/fast
                        and "ask_for_writes" by @acme/config_overlay/safety
```

### Resolution Options

1. **Remove one pack**: `aroom pack remove dev/fast-mode`
2. **Create a higher-precedence overlay**: add a local config overlay that explicitly sets the field
3. **Edit one pack**: change the conflicting value in one pack's overlay

## Reading the Health Check

| Severity | Meaning | Action |
|----------|---------|--------|
| ERROR (`config_conflict`) | Two overlays conflict on the same field | Must fix — ambiguous behavior |
| WARN (`skill_collision`) | Two skills share a name | Review — usually intentional |
| INFO (`shadow`) | Higher layer overrides lower | Informational — expected behavior |

## Tips

- **Intentional overrides**: create a local artifact to override a pack artifact. Don't edit the pack directly (your edits would be lost on the next update)
- **Config conflicts**: avoid by coordinating pack development. If two teams publish config overlays, decide which one owns each field
- **Check regularly**: run `aroom artifact check` after installing or updating packs

## Next Steps

- [Health Check Diagnosis](health-check-diagnosis.md) — fix every issue type
- [Core Concepts: Precedence](../concepts.md#the-6-layer-precedence-stack) — full precedence reference

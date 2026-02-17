---
name: ideate
description: Explore and refine a feature idea before committing to an issue
allowed-tools: Bash, Read, Grep, Glob, Task
---

# /ideate Skill

Have a structured conversation about a feature idea before creating an issue. Explore feasibility, vision alignment, complexity, and alternative approaches — without committing to anything.

## Usage

```
/ideate real-time collaborative canvas editing
/ideate what if we added a plugin system for custom tools
/ideate I want to make search work across multiple instances
```

The argument is a rough idea — it can be vague, ambitious, or half-formed. That's the point.

## Workflow

### Step 1: Understand the Idea

Parse the user's input and restate it back clearly:
- What is the user trying to achieve?
- Who benefits from this?
- What problem does it solve?

Ask clarifying questions if the idea is too vague to evaluate. Keep it to 1-2 targeted questions, not an interrogation.

### Step 2: Vision Check

Read `VISION.md` and evaluate the idea against the product vision.

Check against **"What Anteroom Is Not"** negative guardrails:
- Does this make Anteroom a walled garden?
- Does this make Anteroom more like a ChatGPT clone?
- Does this add configuration burden?
- Does this add enterprise software patterns?
- Does this complicate deployment?
- Does this make Anteroom a model host?

Check against **Out of Scope** (hard no): cloud/SaaS, model training, mobile native, complex deployment, admin dashboards, recreating IDE functionality.

Run the **Litmus Test**:
- Can someone in a locked-down enterprise use this?
- Does it work with `pip install`?
- Is it lean? Could we do this with less?
- Does it work in both interfaces (or have a clear reason not to)?
- Would the team use this daily?

Display the alignment:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🎯 Vision Alignment
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Supports:     <which core principles>
  Guardrails:   ✅ / ⚠️ <any "Is Not" concerns>
  Scope:        ✅ / ❌ <if it hits an out-of-scope area>
  Litmus test:  ✅ / ⚠️ <flag any concerns>

────────────────────────────────────────────
```

Do NOT stop here if there are warnings. This is ideation — explore the tension, don't block on it.

### Step 3: Explore Feasibility (parallel Sonnet agents)

Launch 2 parallel Sonnet agents:

**Agent A — Technical landscape:**
1. Read `CLAUDE.md` for architecture overview
2. Search the codebase for related modules, patterns, and existing infrastructure
3. Identify:
   - What already exists that this idea could build on
   - What would need to be created from scratch
   - Which parts of the codebase would be affected
   - Rough complexity: small (1-2 files), medium (3-5 files), large (6+ files)
4. Flag any technical blockers or dependencies

**Agent B — Prior art and alternatives:**
1. Search existing GitHub issues for related ideas: `gh issue list --search "<keywords>" --state all --limit 10`
2. Check if similar features exist in the current codebase but are undiscovered
3. Think about simpler alternatives that achieve 80% of the goal with 20% of the effort

### Step 4: Present the Analysis

Display a structured analysis:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  💡 Ideation: <idea summary>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📐 Feasibility
  Complexity:     Small / Medium / Large
  Builds on:      <existing modules/patterns>
  New code:       <what would be created>
  Affected files: <list of areas>
  Blockers:       <any technical blockers, or "none">

🔗 Related
  Existing issues: <list any related issues, or "none found">
  Existing code:   <anything already in the codebase that helps>

💡 Approaches
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  A) <Lean approach>
     Complexity: <Small/Medium>
     Trade-offs: <what you give up>
     Vision fit: ✅ / ⚠️

  B) <Full approach>
     Complexity: <Medium/Large>
     Trade-offs: <what you gain vs. cost>
     Vision fit: ✅ / ⚠️

  C) <Alternative framing> (if applicable)
     What if instead of <X> we did <Y>?
     Complexity: <Small/Medium>
     Vision fit: ✅ / ⚠️

────────────────────────────────────────────
```

Always present at least 2 approaches:
- **A lean version** that could ship quickly with minimal complexity
- **A fuller version** that's more complete but more complex

If there are vision concerns, include an alternative framing that avoids them.

### Step 5: Open Discussion

After presenting the analysis, invite the user to discuss:

```
────────────────────────────────────────────
  🗣️ What do you think?

  Options:
    → Discuss further — ask questions, refine the idea
    → Pick an approach — I'll draft as /new-issue
    → Park it — save the idea for later
    → Drop it — this isn't the right direction

────────────────────────────────────────────
```

Stay in conversation mode. The user may want to:
- Ask follow-up questions about feasibility
- Combine approaches
- Refine the scope
- Explore edge cases
- Discuss phasing (v1 lean, v2 full)

Keep responding conversationally until the user makes a decision.

### Step 6: Resolution

Based on the user's decision:

**If "Pick an approach":**
- Confirm which approach (or combination) they chose
- Ask if they want to create the issue now: "Ready for `/new-issue`?"
- If yes, hand off to `/new-issue` with the refined description and chosen approach pre-loaded

**If "Park it":**
- Suggest creating a GitHub issue labeled `idea` or `discussion` to capture the analysis
- Include the vision alignment, feasibility, and approaches in the issue body so it's not lost

**If "Drop it":**
- Acknowledge and move on. No issue created.

**If continuing discussion:**
- Keep the conversation going. No time limit on ideation.

## Guidelines

- This is a **safe space for ideas** — don't shut things down, explore them
- Always present alternatives, even for well-aligned ideas (there might be a leaner way)
- Vision warnings are discussion points, not stop signs
- Be honest about complexity — don't undersell or oversell
- If an idea is genuinely out of scope (cloud hosting, model training), say so clearly but suggest what IS in scope that might address the underlying need
- Keep the tone collaborative, not evaluative — "here's what I found" not "this won't work"

# Projects

Projects group conversations under a shared context with custom system prompts and per-project model selection.

## How Projects Work

Each project is its own world:

- **Project-scoped system prompt** overrides the global default
- **Per-project model override** (or "use global default")
- **Project-scoped folders** --- each project gets its own folder hierarchy
- **"All Conversations" view** to see everything across projects

## Creating a Project

Create a project from the sidebar. Set a name, system prompt, and optionally a model override.

!!! example "Use case"
    Your coding project uses Claude with a developer prompt. Your writing project uses GPT-4 with an editorial voice. Each project maintains its own context.

## Folders

Organize conversations within a project using nested folders:

- Unlimited folder depth
- Add subfolders from the folder context menu
- Collapse/expand state persists to the database
- Depth-based indentation in the sidebar
- Rename and delete (conversations are preserved, not deleted)

## Tags

Color-coded labels for cross-cutting organization:

- Hex color picker for tag colors
- Create tags inline from any conversation's tag dropdown
- Filter the sidebar by tag
- Visual badges with color indicators
- Delete a tag and it's cleanly removed from all conversations

## Deleting a Project

Deleting a project preserves its conversations --- they become unlinked, not deleted. Folders within the project are deleted, but the conversations that were in those folders remain accessible in the "All Conversations" view.

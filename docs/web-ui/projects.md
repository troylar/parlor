# Spaces

Spaces are the universal unit of workspace configuration. They group conversations under a shared context with custom instructions and per-space model selection.

## How Spaces Work

Each space is its own world:

- **Space-scoped instructions** override the global default
- **Per-space model override** (or "use global default")
- **Space-scoped folders** --- each space gets its own folder hierarchy
- **"All Spaces" view** to see everything across spaces

## Creating a Space

Create a space from the sidebar or via `aroom space create`. Set a name, instructions, and optionally a model override.

Spaces can exist in two forms:

- **DB-only** --- created in the web UI or CLI, stored in SQLite. Lightweight and immediate.
- **File-backed** --- defined in a `space.yaml` file. Portable, git-committable, and auto-detected when you `cd` into a mapped directory.

!!! example "Use case"
    Your coding space uses Claude with a developer prompt. Your writing space uses GPT-4 with an editorial voice. Each space maintains its own context.

## Folders

Organize conversations within a space using nested folders:

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

## Deleting a Space

Deleting a space preserves its conversations --- they become unlinked, not deleted. Folders within the space are deleted, but the conversations that were in those folders remain accessible in the "All Spaces" view.

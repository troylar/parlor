# Conversations

Anteroom provides a full conversation lifecycle with powerful management features.

## Creating and Managing

| Action | How |
|---|---|
| **Create** | Click "New Chat" in the sidebar or press `Ctrl+Shift+N` |
| **Rename** | Double-click the conversation title in the sidebar |
| **Search** | Use the search box at the top of the sidebar (FTS5-powered) |
| **Delete** | Right-click or use the context menu on a conversation |
| **Export** | Click the export button to download as Markdown (`.md`) |

## Auto-Titles

After your first message, Anteroom sends the prompt to the AI to generate a short, descriptive title. Titles appear in the sidebar and are stored in the database. Title generation is asynchronous --- failures are silently ignored.

## Per-Conversation Model

Switch models mid-conversation from the top bar dropdown. The new model applies to all subsequent messages in that conversation. Different conversations can use different models simultaneously.

## Fork

Branch a conversation into a new thread from any message. The fork creates a complete copy up to that point, letting you explore alternative directions without losing the original thread.

## Rewind

Roll back to any message in the conversation. All subsequent messages are deleted. Optionally revert file changes made by AI tools via `git checkout` --- useful when the AI made code changes you want to undo.

## Edit and Regenerate

Click the edit button on any user message to modify it. All messages after the edited message are deleted, and the AI regenerates its response from the updated prompt.

## Copy Between Databases

Duplicate an entire conversation (with all messages, tool calls, and attachments) to another [shared database](shared-databases.md). Useful for archiving or sharing conversations between database contexts.

## Full-Text Search

Search is powered by SQLite FTS5. It indexes both message content and conversation titles, providing instant results as you type. Search works across all conversations in the active database.

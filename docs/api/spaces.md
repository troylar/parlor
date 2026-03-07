# Spaces, Folders, and Tags API

## Spaces

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/spaces` | List all spaces |
| `POST` | `/api/spaces` | Create space (name, instructions, model) |
| `GET` | `/api/spaces/:id` | Get space details |
| `DELETE` | `/api/spaces/:id` | Delete space (conversations preserved) |
| `POST` | `/api/spaces/:id/refresh` | Re-parse YAML for file-backed spaces |

## Folders

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/folders` | List folders (use `?space_id=` to filter) |
| `POST` | `/api/folders` | Create folder (name, parent_id, space_id) |
| `PATCH` | `/api/folders/:id` | Update name, parent, collapsed state, position |
| `DELETE` | `/api/folders/:id` | Delete folder + subfolders (conversations preserved) |

## Tags

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/tags` | List all tags |
| `POST` | `/api/tags` | Create tag (name, color) |
| `PATCH` | `/api/tags/:id` | Update name or color |
| `DELETE` | `/api/tags/:id` | Delete tag (removed from all conversations) |
| `POST` | `/api/conversations/:id/tags/:tag_id` | Add tag to conversation |
| `DELETE` | `/api/conversations/:id/tags/:tag_id` | Remove tag from conversation |

# Projects, Folders, and Tags API

## Projects

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/projects` | List all projects |
| `POST` | `/api/projects` | Create project (name, instructions, model) |
| `PATCH` | `/api/projects/:id` | Update name, instructions, or model |
| `DELETE` | `/api/projects/:id` | Delete project (conversations preserved) |

## Folders

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/folders` | List folders (use `?project_id=` to filter) |
| `POST` | `/api/folders` | Create folder (name, parent_id, project_id) |
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

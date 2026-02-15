# Messages and Attachments API

## Messages

| Method | Endpoint | Description |
|---|---|---|
| `PUT` | `/api/messages/:id` | Edit message content (deletes subsequent messages) |
| `DELETE` | `/api/messages/:id` | Delete messages after a position |

### Edit Message

```
PUT /api/messages/:id
```

Updates the message content. All messages after the edited message are deleted, and the AI regenerates from the updated prompt.

### Delete Messages

```
DELETE /api/messages/:id
```

Deletes messages after the specified position in the conversation.

## Attachments

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/attachments/:id` | Download attachment file |

### Download Attachment

```
GET /api/attachments/:id
```

Returns the attachment file. Image attachments are served inline; all other files force-download via `Content-Disposition: attachment`.

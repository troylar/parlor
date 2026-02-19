"""Pydantic models for API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ConversationSummary(BaseModel):
    id: str
    title: str
    type: str = "chat"
    created_at: str
    updated_at: str
    message_count: int


class Conversation(BaseModel):
    id: str
    title: str
    type: str = "chat"
    created_at: str
    updated_at: str


class Attachment(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    url: str | None = None


class ToolCall(BaseModel):
    id: str
    tool_name: str
    server_name: str
    input: dict
    output: dict | None = None
    status: str


class Message(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    position: int
    attachments: list[Attachment] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ConversationDetail(BaseModel):
    id: str
    title: str
    type: str = "chat"
    created_at: str
    updated_at: str
    messages: list[Message] = Field(default_factory=list)


class McpTool(BaseModel):
    name: str
    server_name: str
    description: str
    input_schema: dict


class McpServerStatus(BaseModel):
    name: str
    transport: str
    status: str  # connected, disconnected, error
    tool_count: int
    error_message: str | None = None


class AppConfigResponse(BaseModel):
    ai: dict
    mcp_servers: list[McpServerStatus] = Field(default_factory=list)
    identity: dict | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    folder_id: str | None = Field(default=None, max_length=200)
    type: str | None = Field(default=None, pattern=r"^(chat|note|document)$")


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = Field(default=None, max_length=200)
    project_id: str | None = Field(default=None, max_length=200)


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: str | None = Field(default=None, max_length=200)
    collapsed: bool | None = None
    position: int | None = Field(default=None, ge=0)


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default="#3b82f6", pattern=r"^#[0-9a-fA-F]{6}$")


class TagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class ConnectionValidation(BaseModel):
    valid: bool
    message: str
    models: list[str] = Field(default_factory=list)


class ForkRequest(BaseModel):
    up_to_position: int = Field(ge=0)


class MessageEdit(BaseModel):
    content: str = Field(min_length=1, max_length=100000)


class DatabaseAdd(BaseModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_-]+$")
    path: str = Field(min_length=1, max_length=1000)


class RewindRequest(BaseModel):
    to_position: int = Field(ge=0)
    undo_files: bool = False


class RewindResponse(BaseModel):
    deleted_messages: int
    reverted_files: list[str]
    skipped_files: list[str]


class ConversationCreate(BaseModel):
    title: str = Field(default="New Conversation", min_length=1, max_length=200)
    type: str = Field(default="chat", pattern=r"^(chat|note|document)$")
    project_id: str | None = None


class EntryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100000)


class DocumentContent(BaseModel):
    content: str = Field(min_length=1, max_length=500000)


class CanvasCreate(BaseModel):
    title: str = Field(default="Untitled", min_length=1, max_length=200)
    content: str = Field(default="", max_length=100000)
    language: str | None = Field(default=None, max_length=50, pattern=r"^[a-zA-Z0-9+#_.-]+$")


class CanvasUpdate(BaseModel):
    content: str | None = Field(default=None, max_length=100000)
    title: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "CanvasUpdate":
        if self.content is None and self.title is None:
            raise ValueError("At least one of 'content' or 'title' must be provided")
        return self


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=100000)
    regenerate: bool = False
    source_ids: list[str] = Field(default_factory=list, max_length=50)
    source_tag: str | None = Field(default=None, max_length=200)
    source_group_id: str | None = Field(default=None, max_length=200)


class SourceCreate(BaseModel):
    type: str = Field(pattern=r"^(text|url)$")
    title: str = Field(min_length=1, max_length=500)
    content: str | None = Field(default=None, max_length=500000)
    url: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate_content_or_url(self) -> "SourceCreate":
        if self.type == "text" and not self.content:
            raise ValueError("Content is required for text sources")
        if self.type == "url" and not self.url:
            raise ValueError("URL is required for url sources")
        return self


class SourceUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    content: str | None = Field(default=None, max_length=500000)
    url: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "SourceUpdate":
        if self.title is None and self.content is None and self.url is None:
            raise ValueError("At least one of 'title', 'content', or 'url' must be provided")
        return self


class SourceGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class SourceGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "SourceGroupUpdate":
        if self.name is None and self.description is None:
            raise ValueError("At least one of 'name' or 'description' must be provided")
        return self


class ProjectSourceLink(BaseModel):
    source_id: str | None = Field(default=None, max_length=200)
    group_id: str | None = Field(default=None, max_length=200)
    tag_filter: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _validate_exactly_one(self) -> "ProjectSourceLink":
        non_null = sum(1 for v in (self.source_id, self.group_id, self.tag_filter) if v is not None)
        if non_null != 1:
            raise ValueError("Exactly one of 'source_id', 'group_id', or 'tag_filter' must be provided")
        return self

"""Pydantic models for API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class Conversation(BaseModel):
    id: str
    title: str
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


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    folder_id: str | None = Field(default=None, max_length=200)


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


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=100000)
    regenerate: bool = False

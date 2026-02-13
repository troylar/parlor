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


class AppConfigResponse(BaseModel):
    ai: dict
    mcp_servers: list[McpServerStatus] = Field(default_factory=list)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConnectionValidation(BaseModel):
    valid: bool
    message: str
    models: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=100000)

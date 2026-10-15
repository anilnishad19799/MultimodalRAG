from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    document_id: str | None
    filename: str
    page_number: int | None
    evidence_id: str
    evidence_type: Literal["text", "table", "image", "user_image"]


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    insufficient_evidence: bool = False
    thread_id: str | None = None


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)
    thread_id: str | None = Field(default=None, max_length=36)


class ThreadCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ThreadSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ThreadMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: datetime


class ThreadDetail(ThreadSummary):
    messages: list[ThreadMessage] = Field(default_factory=list)


class MemoryItem(BaseModel):
    id: str
    memory_type: Literal["semantic", "episodic", "procedural", "user_context"]
    content: str
    created_at: datetime


class MemoryForgetResult(BaseModel):
    deleted_memories: int
    deleted_threads: int


class IngestionStatus(BaseModel):
    job_id: str
    document_id: str
    status: Literal["queued", "processing", "completed", "failed"]
    error: str | None = None
    progress_stage: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    progress_message: str | None = None
    progress_percent: int = 0
    progress_details: dict[str, int] = Field(default_factory=dict)

"""HTTP 契约同时用于运行校验、OpenAPI 和前端类型生成。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Content = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
Instruction = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Credentials(Input):
    username: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[a-z0-9_]{3,40}$")]
    password: Annotated[str, StringConstraints(min_length=12, max_length=128)]


class UserOut(BaseModel):
    id: str
    username: str
    csrf_token: str


class CreateDocument(Input):
    title: Title
    content: Content


class Rename(Input):
    title: Title


class Edit(Input):
    content: Content
    base_version_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class Restore(Input):
    base_version_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class CreateTask(Input):
    strategy: Literal["direct", "workflow", "agent"] = "workflow"
    allow_web: bool = False
    source_ids: list[str] = Field(default_factory=list, max_length=8)
    memory_ids: list[str] = Field(default_factory=list, max_length=8)
    instruction: Instruction
    base_version_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class Reject(Input):
    reason: Instruction


class VersionOut(BaseModel):
    id: str
    document_id: str
    number: int
    content: str
    kind: str
    created_at: datetime
    source_task_id: str | None
    restored_from_id: str | None


class DocumentOut(BaseModel):
    id: str
    title: str
    current_version_id: str
    created_at: datetime
    updated_at: datetime
    current_version: VersionOut


class DocumentsOut(BaseModel):
    items: list[DocumentOut]
    total: int


class VersionsOut(BaseModel):
    items: list[VersionOut]
    total: int


class CandidateOut(BaseModel):
    text: str
    reason: str


class RunOut(BaseModel):
    number: int
    provider: str
    model: str
    prompt_version: str
    duration_ms: int | None
    usage: dict | None
    outcome: str
    error_code: str | None


class TaskOut(BaseModel):
    evidence_audit: dict = Field(default_factory=dict)
    allow_web: bool = False
    strategy: str
    trace: list[dict]
    citations: list[dict]
    context_items: list[dict]
    id: str
    document_id: str
    base_version_id: str
    source_text: str
    instruction: str
    status: Literal["queued", "running", "review", "accepted", "rejected", "failed", "cancelled"]
    attempts: int
    previous_task_id: str | None
    rejection_reason: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    decided_at: datetime | None
    candidate: CandidateOut | None
    runs: list[RunOut]


class MetaOut(BaseModel):
    web_search_available: bool = False
    semantic_search_available: bool = False
    version: str
    provider: str
    model: str
    max_content_chars: int


class ErrorOut(BaseModel):
    detail: str


class MessageOut(BaseModel):
    message: str


class KnowledgeInput(Input):
    kind: Literal["source", "memory"]
    title: Title
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)]
    expires_at: datetime | None = Field(default=None, strict=False)


class KnowledgeOut(BaseModel):
    provenance: dict
    index_mode: str
    id: str
    kind: str
    title: str
    content: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ResearchInput(Input):
    kind: Literal["file", "url", "reindex"]
    title: Title
    url: str = Field(default="", max_length=2000)
    filename: str = Field(default="", max_length=200)
    content_base64: str = Field(default="", max_length=2_666_668)
    knowledge_id: str = Field(default="", max_length=36)


class ResearchOut(BaseModel):
    id: str
    kind: str
    title: str
    status: str
    progress: str
    error: str | None
    knowledge_id: str | None
    attempts: int
    created_at: datetime
    finished_at: datetime | None

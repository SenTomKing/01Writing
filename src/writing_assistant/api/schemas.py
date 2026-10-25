"""接口输入与输出，只开放当前操作允许的字段。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import ValidationError
from ..validation import DEFAULT_MAX_INPUT_CHARS, DEFAULT_MAX_TITLE_CHARS, validate_document_content, validate_document_title


class DocumentTitleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(max_length=DEFAULT_MAX_TITLE_CHARS, description="文档标题")

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> str:
        try:
            return validate_document_title(value)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc


class CreateDocumentInput(DocumentTitleInput):
    initial_content: str = Field(max_length=DEFAULT_MAX_INPUT_CHARS, description="初始正文")

    @field_validator("initial_content", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> str:
        try:
            return validate_document_content(value)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc


class DocumentSummary(BaseModel):
    id: str
    title: str
    current_version_id: str
    created_at: str
    updated_at: str


class TextVersionResponse(BaseModel):
    id: str
    document_id: str
    version_number: int
    content: str
    created_at: str
    source_revision_id: str | None = None
    restored_from_version_id: str | None = None


class DocumentDetail(DocumentSummary):
    current_version: TextVersionResponse


class DocumentPage(BaseModel):
    items: list[DocumentSummary]
    total: int
    page: int
    page_size: int


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: str
    database: str

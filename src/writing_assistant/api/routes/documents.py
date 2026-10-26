"""创建、分页查询、打开文档与修改标题的接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ...document_workflow import DocumentCatalogWorkflow
from ...validation import DEFAULT_MAX_INPUT_CHARS
from ..dependencies import DocumentStoreDependency
from ..schemas import CreateDocumentInput, DocumentDetail, DocumentPage, DocumentSummary, DocumentTitleInput, ErrorResponse


router = APIRouter(prefix="/api/documents", tags=["文档"], responses={
    404: {"model": ErrorResponse, "description": "文档不存在"},
    500: {"model": ErrorResponse, "description": "存储操作失败"},
})


@router.post("", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED, summary="创建文档与初始正文")
def create_document(body: CreateDocumentInput, response: Response, store: DocumentStoreDependency):
    workflow = DocumentCatalogWorkflow(store=store, max_content_chars=DEFAULT_MAX_INPUT_CHARS)
    document, version = workflow.create_document(title=body.title, initial_content=body.initial_content)
    response.headers["Location"] = f"/api/documents/{document.id}"
    return {**document.to_dict(), "current_version": version.to_dict()}


@router.get("", response_model=DocumentPage, summary="分页列出文档（最新创建优先）")
def list_documents(
    store: DocumentStoreDependency,
    page: Annotated[int, Query(ge=1, le=1_000_000, description="从 1 开始的页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
):
    documents, total = store.list_page(page=page, page_size=page_size)
    return {"items": [document.to_dict() for document in documents], "total": total, "page": page, "page_size": page_size}


@router.get("/{document_id}", response_model=DocumentDetail, summary="打开文档及当前正文")
def open_document(document_id: UUID, store: DocumentStoreDependency):
    workflow = DocumentCatalogWorkflow(store=store, max_content_chars=DEFAULT_MAX_INPUT_CHARS)
    document, version = workflow.open_document(str(document_id))
    return {**document.to_dict(), "current_version": version.to_dict()}


@router.patch("/{document_id}", response_model=DocumentSummary, summary="修改文档标题")
def rename_document(document_id: UUID, body: DocumentTitleInput, store: DocumentStoreDependency):
    return store.rename_document(str(document_id), body.title).to_dict()

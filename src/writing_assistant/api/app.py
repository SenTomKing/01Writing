"""创建本地 FastAPI 服务，启动时初始化版本化数据库结构。"""

import argparse
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
import uvicorn

from .. import __version__
from ..config import get_project_root
from ..errors import DocumentNotFoundError, StorageError, ValidationError
from ..storage.database import Database
from .dependencies import SessionDependency
from .routes.documents import router
from .schemas import ErrorResponse, HealthResponse


logger = logging.getLogger(__name__)


def create_app(database_path: Path | None = None) -> FastAPI:
    """构造应用时不连接数据库；测试可以传入独立文件路径。"""

    if database_path is None:
        configured = os.getenv("WRITING_DATABASE_FILE", "data/writing.db").strip()
        if not configured:
            raise ValueError("WRITING_DATABASE_FILE 不能为空")
        database_path = Path(configured)
        if not database_path.is_absolute():
            database_path = get_project_root() / database_path
    database = Database(database_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            database.initialize()
            yield
        finally:
            database.dispose()

    app = FastAPI(title="01Writing 文档后端", version=__version__, lifespan=lifespan)
    app.state.database = database

    @app.exception_handler(DocumentNotFoundError)
    async def document_not_found(_request: Request, exc: DocumentNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def invalid_input(_request: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    async def storage_failure(_request: Request, exc: Exception):
        logger.error("数据库操作失败", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(status_code=500, content={"detail": "数据库操作失败，请稍后重试"})

    app.add_exception_handler(StorageError, storage_failure)
    app.add_exception_handler(SQLAlchemyError, storage_failure)
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/docs")

    @app.get("/health", response_model=HealthResponse, tags=["服务"], summary="检查服务与数据库", responses={503: {"model": ErrorResponse}})
    def health(session: SessionDependency):
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"detail": "数据库暂不可用"})
        return {"status": "ok", "database": "ok"}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 01Writing 文档后端")
    parser.add_argument("--database", type=Path, help="数据库文件路径（默认 data/writing.db）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    args = parser.parse_args()
    uvicorn.run(create_app(args.database), host=args.host, port=args.port)

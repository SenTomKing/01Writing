"""网页 API：所有业务资源都按当前账户授权，状态变更使用短事务。"""

from contextlib import asynccontextmanager
import json
import logging
import secrets
import time
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from . import __version__, schemas as dto, services, knowledge
from .database import Database
from .security import COOKIE, CSRF_COOKIE, PASSWORDS, digest, identity, issue_session, rate_limit, verify_password
from .settings import Settings
from .tables import Document, Knowledge, LoginSession, Task, TaskEvent, User, Version

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.load()
    db = Database(settings)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            db.close()

    app = FastAPI(
        title="01Writing",
        version=__version__,
        lifespan=lifespan,
        responses={
            401: {"model": dto.ErrorOut},
            403: {"model": dto.ErrorOut},
            404: {"model": dto.ErrorOut},
            409: {"model": dto.ErrorOut},
            429: {"model": dto.ErrorOut},
        },
    )
    app.state.database, app.state.settings = db, settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        request_id = secrets.token_hex(12)
        if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in settings.origins:
                return JSONResponse({"detail": "请求来源不受允许"}, 403)
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 128_000:
                    return JSONResponse({"detail": "请求内容过大"}, 413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        logger.info(
            "request=%s method=%s status=%s duration_ms=%s",
            request_id,
            request.method,
            response.status_code,
            int((time.monotonic() - started) * 1000),
        )
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        # 不记录 SQL 参数、原稿、密码或连接 URL。
        logger.error("database_failure type=%s", type(exc).__name__)
        return JSONResponse({"detail": "数据库暂时不可用，请稍后重试"}, 503)

    def get_session():
        with db.sessions.begin() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session, scope="function")]
    Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]

    def auth(request, session, write=False):
        return identity(request, session, write=write)[0]

    @app.get("/api/v1/meta", response_model=dto.MetaOut)
    def meta():
        return {
            "version": __version__,
            "provider": settings.provider,
            "model": settings.model if settings.provider == "deepseek" else "demo-v1",
            "max_content_chars": 20_000,
        }

    @app.get("/health")
    def health():
        try:
            with db.sessions() as session:
                revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "v2_agent":
                raise ValueError("schema")
        except (SQLAlchemyError, ValueError):
            return JSONResponse({"status": "unavailable", "detail": "请检查数据库并先执行迁移"}, 503)
        return {"status": "ok", "version": __version__, "database": "ok"}

    @app.post("/api/v1/auth/register", response_model=dto.UserOut, status_code=201)
    def register(data: dto.Credentials, request: Request, response: Response, session: SessionDep):
        rate_limit(db, "register", request.client.host, limit=10, seconds=600)
        user = User(username=data.username, password_hash=PASSWORDS.hash(data.password))
        session.add(user)
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(409, "用户名已被使用")
        return issue_session(session, user, response, settings)

    @app.post("/api/v1/auth/login", response_model=dto.UserOut)
    def login(data: dto.Credentials, request: Request, response: Response, session: SessionDep):
        rate_limit(db, "login", f"{request.client.host}:{data.username}", limit=10, seconds=600)
        rate_limit(db, "login-ip", request.client.host, limit=40, seconds=600)
        user = session.scalar(select(User).where(User.username == data.username))
        if not verify_password(user.password_hash if user else None, data.password) or not user:
            raise HTTPException(401, "用户名或密码错误")
        old = request.cookies.get(COOKIE)
        if old:
            existing = session.get(LoginSession, digest(old))
            if existing:
                session.delete(existing)
        return issue_session(session, user, response, settings)

    @app.get("/api/v1/auth/me", response_model=dto.UserOut)
    def me(request: Request, session: SessionDep):
        user = auth(request, session)
        # 返回同源页面可读取的 CSRF 值；真正的写请求会与会话中保存的哈希核对。
        csrf = request.cookies.get(CSRF_COOKIE, "")
        return {"id": user.id, "username": user.username, "csrf_token": csrf}

    @app.post("/api/v1/auth/logout", response_model=dto.MessageOut)
    def logout(request: Request, response: Response, session: SessionDep):
        _, login_session = identity(request, session, write=True)
        session.delete(login_session)
        response.delete_cookie(COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"message": "已退出登录"}

    @app.get("/api/v1/knowledge", response_model=list[dto.KnowledgeOut])
    def list_knowledge(request: Request, session: SessionDep):
        user = auth(request, session)
        return [knowledge.data(row) for row in knowledge.entries(session, user.id)]

    @app.post("/api/v1/knowledge", response_model=dto.KnowledgeOut, status_code=201)
    def add_knowledge(data: dto.KnowledgeInput, request: Request, session: SessionDep):
        user = auth(request, session, True)
        return knowledge.data(knowledge.save(session, user.id, data))

    @app.patch("/api/v1/knowledge/{entry_id}", response_model=dto.KnowledgeOut)
    def edit_knowledge(entry_id: UUID, data: dto.KnowledgeInput, request: Request, session: SessionDep):
        user = auth(request, session, True)
        return knowledge.data(knowledge.save(session, user.id, data, str(entry_id)))

    @app.delete("/api/v1/knowledge/{entry_id}", response_model=dto.MessageOut)
    def delete_knowledge(entry_id: UUID, request: Request, session: SessionDep):
        user = auth(request, session, True)
        row = session.scalar(select(Knowledge).where(Knowledge.id == str(entry_id), Knowledge.user_id == user.id))
        if not row:
            raise HTTPException(404, "找不到资料或记忆")
        session.delete(row)
        return {"message": "已删除，未来任务不再使用；已有任务保留当时的依据快照"}

    @app.get("/api/v1/documents", response_model=dto.DocumentsOut)
    def documents(
        request: Request,
        session: SessionDep,
        page: int = Query(1, ge=1, le=10000),
        limit: int = Query(30, ge=1, le=100),
    ):
        user = auth(request, session)
        scope = Document.user_id == user.id
        rows = session.scalars(
            select(Document)
            .where(scope)
            .order_by(Document.updated_at.desc(), Document.id)
            .offset((page - 1) * limit)
            .limit(limit)
        ).all()
        return {
            "items": [services.document_data(session, row) for row in rows],
            "total": session.scalar(select(func.count()).select_from(Document).where(scope)),
        }

    @app.post("/api/v1/documents", response_model=dto.DocumentOut, status_code=201)
    def create_document(data: dto.CreateDocument, request: Request, session: SessionDep):
        user = auth(request, session, True)
        return services.document_data(session, services.create_document(session, user.id, data.title, data.content))

    @app.get("/api/v1/documents/{document_id}", response_model=dto.DocumentOut)
    def open_document(document_id: UUID, request: Request, session: SessionDep):
        user = auth(request, session)
        return services.document_data(session, services.owned_document(session, user.id, str(document_id)))

    @app.patch("/api/v1/documents/{document_id}", response_model=dto.DocumentOut)
    def rename(document_id: UUID, data: dto.Rename, request: Request, session: SessionDep):
        user = auth(request, session, True)
        document = services.owned_document(session, user.id, str(document_id), lock=True)
        document.title = data.title
        return services.document_data(session, document)

    @app.post("/api/v1/documents/{document_id}/versions", response_model=dto.DocumentOut)
    def edit(document_id: UUID, data: dto.Edit, request: Request, session: SessionDep):
        user = auth(request, session, True)
        document = services.owned_document(session, user.id, str(document_id), lock=True)
        services.append_version(session, document, data.content, base_version_id=data.base_version_id)
        return services.document_data(session, document)

    @app.get("/api/v1/documents/{document_id}/versions", response_model=dto.VersionsOut)
    def versions(
        document_id: UUID,
        request: Request,
        session: SessionDep,
        page: int = Query(1, ge=1, le=10000),
        limit: int = Query(30, ge=1, le=100),
    ):
        user = auth(request, session)
        document = services.owned_document(session, user.id, str(document_id))
        rows = session.scalars(
            select(Version)
            .where(Version.document_id == document.id)
            .order_by(Version.number.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        ).all()
        return {
            "items": [services.version_data(row) for row in rows],
            "total": session.scalar(
                select(func.count()).select_from(Version).where(Version.document_id == document.id)
            ),
        }

    @app.post("/api/v1/documents/{document_id}/versions/{version_id}/restore", response_model=dto.DocumentOut)
    def restore(document_id: UUID, version_id: UUID, data: dto.Restore, request: Request, session: SessionDep):
        user = auth(request, session, True)
        document = services.owned_document(session, user.id, str(document_id), lock=True)
        version = session.scalar(
            select(Version).where(Version.id == str(version_id), Version.document_id == document.id)
        )
        if not version:
            raise HTTPException(404, "找不到历史版本")
        services.append_version(
            session,
            document,
            version.content,
            base_version_id=data.base_version_id,
            kind="restored",
            restored_from_id=version.id,
        )
        return services.document_data(session, document)

    @app.get("/api/v1/documents/{document_id}/export")
    def export(
        document_id: UUID, request: Request, session: SessionDep, format: str = Query("md", pattern="^(md|txt)$")
    ):
        user = auth(request, session)
        document = services.owned_document(session, user.id, str(document_id))
        content = session.get(Version, document.current_version_id).content
        body = f"# {document.title}\n\n{content}\n" if format == "md" else content
        return Response(
            body,
            media_type="text/markdown" if format == "md" else "text/plain",
            headers={"Content-Disposition": f'attachment; filename="document-{document.id}.{format}"'},
        )

    @app.post("/api/v1/documents/{document_id}/tasks", response_model=dto.TaskOut, status_code=202)
    def create_task(document_id: UUID, data: dto.CreateTask, request: Request, session: SessionDep, key: Key):
        user = auth(request, session, True)
        task = services.create_task(
            session, user.id, str(document_id), data.instruction, data.base_version_id, key, settings,
            strategy=data.strategy, source_ids=data.source_ids, memory_ids=data.memory_ids
        )
        return services.task_data(session, task)

    @app.get("/api/v1/documents/{document_id}/tasks", response_model=list[dto.TaskOut])
    def tasks(document_id: UUID, request: Request, session: SessionDep, page: int = Query(1, ge=1, le=10000)):
        user = auth(request, session)
        document = services.owned_document(session, user.id, str(document_id))
        rows = session.scalars(
            select(Task)
            .where(Task.document_id == document.id)
            .order_by(Task.created_at.desc(), Task.id)
            .offset((page - 1) * 30)
            .limit(30)
        ).all()
        return [services.task_data(session, row) for row in rows]

    @app.get("/api/v1/tasks/{task_id}", response_model=dto.TaskOut)
    def task_detail(task_id: UUID, request: Request, session: SessionDep):
        user = auth(request, session)
        return services.task_data(session, services.owned_task(session, user.id, str(task_id)))

    @app.post("/api/v1/tasks/{task_id}/accept", response_model=dto.TaskOut)
    def accept(task_id: UUID, request: Request, session: SessionDep):
        user = auth(request, session, True)
        return services.task_data(session, services.decide(session, user.id, str(task_id), "accept"))

    @app.post("/api/v1/tasks/{task_id}/reject", response_model=dto.TaskOut)
    def reject(task_id: UUID, data: dto.Reject, request: Request, session: SessionDep):
        user = auth(request, session, True)
        return services.task_data(session, services.decide(session, user.id, str(task_id), "reject", data.reason))

    @app.post("/api/v1/tasks/{task_id}/cancel", response_model=dto.TaskOut)
    def cancel(task_id: UUID, request: Request, session: SessionDep):
        user = auth(request, session, True)
        return services.task_data(session, services.cancel(session, user.id, str(task_id)))

    @app.post("/api/v1/tasks/{task_id}/retry", response_model=dto.TaskOut, status_code=202)
    def retry(task_id: UUID, request: Request, session: SessionDep, key: Key):
        user = auth(request, session, True)
        old = services.owned_task(session, user.id, str(task_id))
        if old.status not in {"failed", "rejected"}:
            raise HTTPException(409, "只有失败或拒绝的任务可以重新生成")
        previous = old.id if old.status == "rejected" else old.previous_task_id
        task = services.create_task(
            session,
            user.id,
            old.document_id,
            old.instruction,
            old.base_version_id,
            key,
            settings,
            previous_task_id=previous,
            strategy=old.strategy,
            source_ids=[item["id"] for item in (old.context or {}).get("sources", [])],
            memory_ids=[item["id"] for item in (old.context or {}).get("memories", [])],
        )
        return services.task_data(session, task)

    @app.get("/api/v1/tasks/{task_id}/events")
    def events(task_id: UUID, request: Request, after: int = Query(0, ge=0)):
        with db.sessions.begin() as session:
            user = auth(request, session)
            services.owned_task(session, user.id, str(task_id))
        last = request.headers.get("Last-Event-ID", "")
        if last.isdigit():
            after = max(after, int(last))

        def stream():
            cursor = after
            # 定期结束连接，让客户端重连，同时重新校验登录状态。
            for _ in range(12):
                with db.sessions.begin() as session:
                    try:
                        current_user = auth(request, session)
                        task = services.owned_task(session, current_user.id, str(task_id))
                    except HTTPException:
                        return
                    rows = session.scalars(
                        select(TaskEvent)
                        .where(TaskEvent.task_id == task.id, TaskEvent.id > cursor)
                        .order_by(TaskEvent.id)
                        .limit(100)
                    ).all()
                    messages = [(row.id, row.kind, row.data) for row in rows]
                    status = task.status
                for event_id, kind, data in messages:
                    cursor = event_id
                    yield f"id: {event_id}\nevent: progress\ndata: {json.dumps({'kind': kind, 'status': status, **data}, ensure_ascii=False)}\n\n"
                if status not in {"queued", "running"}:
                    return
                yield ": keepalive\n\n"
                time.sleep(1)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"}
        )

    if settings.static_dir and (settings.static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
    return app

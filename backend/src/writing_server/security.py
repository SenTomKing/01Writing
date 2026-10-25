"""不透明会话、密码哈希与数据库限流。"""

from datetime import timedelta
import hashlib
import hmac
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException, Request, Response
from sqlalchemy.dialects.postgresql import insert

from .tables import LoginSession, RateBucket, User, now

PASSWORDS = PasswordHasher()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))
COOKIE = "writing_session"
CSRF_COOKIE = "writing_csrf"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def verify_password(encoded, password):
    try:
        return PASSWORDS.verify(encoded or DUMMY_HASH, password)
    except VerificationError:
        return False


def rate_limit(db, scope, subject, *, limit, seconds=60):
    """限流独立提交，因此后续登录失败不能回滚计数。"""
    bucket = int(time.time()) // seconds
    key = digest(f"{scope}:{subject}:{bucket}")
    with db.sessions.begin() as session:
        count = session.scalar(
            insert(RateBucket)
            .values(key=key, count=1, expires_at=now() + timedelta(seconds=seconds * 2))
            .on_conflict_do_update(index_elements=[RateBucket.key], set_={"count": RateBucket.count + 1})
            .returning(RateBucket.count)
        )
    if count > limit:
        raise HTTPException(429, "操作过于频繁，请稍后重试", headers={"Retry-After": str(seconds)})


def issue_session(session, user, response: Response, settings):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session.add(
        LoginSession(
            token_hash=digest(token),
            user_id=user.id,
            csrf_hash=digest(csrf),
            expires_at=now() + timedelta(hours=settings.session_hours),
        )
    )
    options = dict(secure=settings.secure_cookie, samesite="lax", path="/", max_age=settings.session_hours * 3600)
    response.set_cookie(COOKIE, token, httponly=True, **options)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **options)
    return {"id": user.id, "username": user.username, "csrf_token": csrf}


def identity(request: Request, session, *, write=False):
    token = request.cookies.get(COOKIE)
    login = session.get(LoginSession, digest(token)) if token else None
    if not login or login.expires_at <= now():
        raise HTTPException(401, "请先登录")
    if write:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(login.csrf_hash, digest(supplied)):
            raise HTTPException(403, "页面验证已失效，请刷新后重试")
    user = session.get(User, login.user_id)
    if user is None:
        raise HTTPException(401, "登录已失效")
    return user, login

"""个人凭证解析边界：认证加密、账号绑定、版本栅栏，禁止全局密钥回退。"""
from dataclasses import replace
import json
import re

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select

from .generation import ModelFailure
from .tables import User, UserCredential, now, uid

DEEPSEEK_URL = "https://api.deepseek.com"
EMBEDDING_URL = "https://api.siliconflow.cn/v1"
EMBEDDING_MODEL = "BAAI/bge-m3"


def cipher(settings):
    try:
        return Fernet(settings.credential_master_key.encode('ascii'))
    except (ValueError, UnicodeError):
        raise ModelFailure('credential_storage_unavailable', '个人密钥存储未配置，请联系部署者') from None


def decode(settings, row):
    try:
        data = json.loads(cipher(settings).decrypt(row.ciphertext.encode()))
        if data['user_id'] != row.user_id or data['revision'] != row.revision:
            raise ValueError('binding')
        for name in ('deepseek_key', 'embedding_key'):
            if not isinstance(data[name], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{16,512}', data[name]):
                raise ValueError('format')
        return data
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
        raise ModelFailure('credential_unreadable', '无法读取个人密钥，请重新保存或联系部署者检查加密配置') from None


def status(session, settings, user_id):
    row = session.get(UserCredential, user_id)
    storage_ready, ready, error = True, False, None
    try:
        cipher(settings)
        if row:
            decode(settings, row)
            ready = True
    except ModelFailure as exc:
        storage_ready = exc.code != 'credential_storage_unavailable'
        error = exc.message
    return {'configured': ready, 'storage_ready': storage_ready, 'required': settings.provider != 'demo',
            'deepseek_mask': '••••••••（已保存）' if ready else '',
            'embedding_mask': '••••••••（已保存）' if ready else '',
            'model': 'deepseek-flash', 'embedding_model': EMBEDDING_MODEL,
            'updated_at': row.updated_at if row else None, 'error': error}


def save(session, settings, user_id, deepseek_key, embedding_key):
    session.scalar(select(User).where(User.id == user_id).with_for_update())
    keys = [deepseek_key.strip(), embedding_key.strip()]
    if any(not re.fullmatch(r'[A-Za-z0-9_.-]{16,512}', key) for key in keys):
        raise HTTPException(422, '两个密钥均须为16—512位字母、数字或 -_. 字符，请检查复制是否完整')
    revision = uid()
    payload = {'user_id': user_id, 'revision': revision, 'deepseek_key': keys[0], 'embedding_key': keys[1]}
    encrypted = cipher(settings).encrypt(json.dumps(payload).encode()).decode()
    row = session.get(UserCredential, user_id)
    if row is None:
        row = UserCredential(user_id=user_id)
        session.add(row)
    row.revision, row.ciphertext, row.updated_at = revision, encrypted, now()
    session.flush()
    return status(session, settings, user_id)


def remove(session, user_id):
    session.scalar(select(User).where(User.id == user_id).with_for_update())
    row = session.get(UserCredential, user_id)
    if row:
        session.delete(row)


def resolve(session, settings, user_id, *, expected_revision=None):
    # 离线演示绝不调用真实供应商，包括资料索引。
    if settings.provider == 'demo':
        return replace(settings, api_key='', search_key='', search_provider='none',
                       embedding_key='', embedding_url='', embedding_model='', credential_revision='')
    row = session.get(UserCredential, user_id)
    if row is None:
        raise ModelFailure('credentials_required', '请先在“模型设置”保存自己的 DeepSeek 和硅基流动密钥')
    if expected_revision is not None and row.revision != expected_revision:
        raise ModelFailure('credentials_changed', '密钥已更换或任务未绑定个人凭证，请重新提交任务')
    data = decode(settings, row)
    return replace(settings, provider='deepseek', api_key=data['deepseek_key'], base_url=DEEPSEEK_URL,
                   model='deepseek-flash', search_key='', search_provider='deepseek',
                   embedding_key=data['embedding_key'], embedding_url=EMBEDDING_URL,
                   embedding_model=EMBEDDING_MODEL, credential_revision=row.revision)


def for_request(session, settings, user_id):
    try:
        return resolve(session, settings, user_id)
    except ModelFailure as exc:
        raise HTTPException(409 if exc.code == 'credentials_required' else 503, exc.message) from None

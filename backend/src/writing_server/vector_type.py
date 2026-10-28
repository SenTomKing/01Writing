"""使用已安装在public模式的pgvector；无需把应用角色提升为管理员。"""
import json
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    cache_ok=True

    def get_col_spec(self, **kwargs):
        return 'public.vector'

    def bind_processor(self,dialect):
        return lambda value:None if value is None else json.dumps(value,allow_nan=False)

    def result_processor(self,dialect,coltype):
        return lambda value:json.loads(value) if isinstance(value,str) else value

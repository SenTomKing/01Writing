"""修订记录存储。"""

from .base import RevisionStore
from .json_store import JsonRevisionStore

__all__ = ["RevisionStore", "JsonRevisionStore"]

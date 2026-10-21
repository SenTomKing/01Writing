"""文本候选生成器。"""

from .base import CandidateGenerator
from .deepseek import DeepSeekCandidateGenerator

__all__ = ["CandidateGenerator", "DeepSeekCandidateGenerator"]

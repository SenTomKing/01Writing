"""项目使用的异常类型。"""


class WritingAssistantError(Exception):
    """项目内可预期错误的基类。"""


class ValidationError(WritingAssistantError):
    """输入或候选内容没有通过校验。"""


class ConfigurationError(WritingAssistantError):
    """环境变量或项目配置不完整。"""


class GenerationError(WritingAssistantError):
    """调用模型或解析模型响应失败。"""


class StorageError(WritingAssistantError):
    """读取或保存本地项目数据失败。"""


class RecordNotFoundError(StorageError):
    """找不到指定修订记录。"""


class DocumentNotFoundError(StorageError):
    """找不到指定文档。"""


class TextVersionNotFoundError(StorageError):
    """找不到指定正文版本。"""


class InvalidDecisionError(WritingAssistantError):
    """修订记录当前不能执行所请求的决定。"""


class VersionConflictError(InvalidDecisionError):
    """候选所基于的版本已经过期，不能覆盖当前正文。"""

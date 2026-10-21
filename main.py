"""01Writing 的便捷启动入口。

正式代码放在 src/writing_assistant 中。保留这个文件，是为了让刚接触
src 结构的使用者仍然可以直接运行 `python main.py`。
"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIRECTORY = PROJECT_ROOT / "src"

# 直接运行源码仓库时，Python 默认不会搜索 src，因此在入口处补充路径。
if str(SRC_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SRC_DIRECTORY))

from writing_assistant.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

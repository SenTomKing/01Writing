"""导出接口，供前端生成类型；无需连接数据库。"""

from pathlib import Path
import json
from writing_server.app import create_app

app = create_app()
root = Path(__file__).resolve().parents[1]
(root / "docs").mkdir(exist_ok=True)
(root / "docs/openapi.json").write_text(
    json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
app.state.database.close()

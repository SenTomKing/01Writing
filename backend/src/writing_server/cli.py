"""迁移、服务、任务与旧数据导入的管理入口。"""

import argparse
import json
from pathlib import Path

from .database import Database
from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description="01Writing 网页服务管理")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="使用建表账户执行迁移")
    api = commands.add_parser("serve", help="启动网页 API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    worker = commands.add_parser("dispatch", help="任务投递及中断恢复")
    worker.add_argument(
        "--local", action="store_true", help="仅本机开发：直接执行数据库任务，不启动原生 Windows Celery"
    )
    worker.add_argument("--once", action="store_true")
    commands.add_parser("openapi", help="输出公开接口契约 JSON")
    importer = commands.add_parser("import-json", help="显式导入旧版项目 JSON，保留源文件")
    importer.add_argument("file", type=Path)
    importer.add_argument("--username", required=True)
    args = parser.parse_args()
    settings = Settings.load()
    if args.command == "migrate":
        db = Database(settings, migration=True)
        try:
            db.migrate()
            print("数据库迁移完成")
        finally:
            db.close()
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("writing_server.app:create_app", factory=True, host=args.host, port=args.port)
    elif args.command == "dispatch":
        from .worker import dispatch_loop

        dispatch_loop(local=args.local, once=args.once)
    elif args.command == "openapi":
        from .app import create_app

        print(json.dumps(create_app(settings).openapi(), ensure_ascii=False, indent=2))
    elif args.command == "import-json":
        from .importer import import_json

        db = Database(settings)
        try:
            print(json.dumps(import_json(db, args.file, args.username), ensure_ascii=False, indent=2))
        finally:
            db.close()


if __name__ == "__main__":
    main()

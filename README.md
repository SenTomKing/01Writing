# 01Writing

一个带人工确认、版本历史和后台任务的 AI 写作工作台。用户在网页中保存正文、提出修改要求、比较候选，接受后形成新版本；拒绝原因会进入下一轮修改。历史恢复追加快照，不覆盖旧稿。

## v1.0 功能

- 账户注册、登录、退出，文档、历史、任务、进度与导出按账户隔离。
- 文档创建、改标题、正文编辑保存、Markdown／TXT 导出。
- 后台生成、差异展示、人工接受、拒绝反馈与重新生成、取消和失败重试。
- 版本冲突检测、重复生成请求去重、重复接受不追加第二份版本。
- PostgreSQL 事务、关系约束、Alembic 迁移，执行租约、事件记录、有限恢复。
- React／TypeScript 网页，OpenAPI 生成的接口类型，SSE 与查询展示进度。
- 显式导入旧 JSON，保留源文件；基础 A／B 评测与数据库备份入口。

技术栈：Python、FastAPI、Pydantic、SQLAlchemy、PostgreSQL、Celery、Redis、React、TypeScript、Vite、Docker Compose。架构与边界见 [架构说明](docs/architecture.md)，评测方法见 [evals](evals/README.md)。本版是有界修订工作流，资料检索、引用核验和动态 Agent 工具循环留待后续版本。

## 用 Docker 启动完整系统

需要 Docker Desktop／Linux Docker 与 Compose。Windows 使用 Linux 容器和可运行的 WSL2。

```powershell
git clone https://github.com/SenTomKing/01Writing.git
cd 01Writing
python deploy/prepare_env.py
docker compose --env-file .env.deploy up --build -d
docker compose --env-file .env.deploy ps
```

打开 **http://localhost:8080**，创建自己的账户。密码至少12字符；用户名使用3—40位小写字母、数字或下划线。默认 `demo` 离线演示不调用模型，仅做简单字符清理，页面会明确标注。

启用 DeepSeek：在本机 `.env.deploy` 填写 `DEEPSEEK_API_KEY` 并将 `WRITING_PROVIDER` 改为 `deepseek`，重新运行启动命令。密钥只进入服务端，不放到前端变量或仓库。PostgreSQL 与 Redis 不映射公网端口；网页默认只监听本机。

```powershell
docker compose --env-file .env.deploy logs --tail=100 api worker dispatcher
docker compose --env-file .env.deploy down
```

`down` 保留数据库卷；不要使用 `down -v` 作为日常停止方式。已有数据卷不会再次运行初始化脚本，改环境变量不会自动重设旧库密码。升级前备份，更新代码后重新构建，迁移服务成功以后 API 才启动。

## 本机开发：复用已安装的 PostgreSQL

建议 Python 3.13、Node.js 24、pnpm 10.20。先准备 `writing` 数据库，迁移账户能建表，应用账户能读写；给迁移账户创建的新表和序列设置默认授权。连接参数放在 `.env.postgres.local`，模型参数可放在 `.env`，格式参考 `.env.example`。

```powershell
uv sync --locked --extra test
uv run writing-web migrate
uv run writing-web serve
```

另开终端执行本机开发运行器：

```powershell
uv run writing-web dispatch --local
```

再开终端启动网页：

```powershell
cd frontend
npx --yes pnpm@10.20.0 install --frozen-lockfile
npx --yes pnpm@10.20.0 dev
```

打开 **http://127.0.0.1:5173**。开发网页把 `/api` 请求代理到8000端口。也可以构建网页后，由 API 提供静态页面：

```powershell
cd frontend
npx --yes pnpm@10.20.0 build
cd ..
uv run writing-web serve
```

此时访问 **http://127.0.0.1:8000**。`dispatch --local` 是便于本机开发的单进程执行方式，不等同于 Linux 的 Celery／Redis 验证。正式运行采用 Compose 中的 API、worker、dispatcher 和 Redis。

## 验证

```powershell
uv run pytest -q
uv run ruff check backend/src backend/tests --select E9,F
uv run python scripts/export_openapi.py
cd frontend
npx --yes pnpm@10.20.0 generate-api
npx --yes pnpm@10.20.0 build
npx --yes pnpm@10.20.0 exec playwright install chromium
cd ..
node scripts/browser-smoke.mjs
```

数据库测试需要本机 PostgreSQL 和项目角色，只在随机新建的测试模式中运行与清理，不清空 public。浏览器验证需要已启动的服务和任务执行器，默认地址5173；设置 `WRITING_WEB_URL` 可改地址。它会创建独立测试账户和示例文档。

CI 在 Linux 上运行 PostgreSQL 测试、前端构建、真实 Redis／Celery 与浏览器链路，并构建部署镜像；查看具体提交的工作流结果，不能把配置存在等同于运行通过。

## 效果评测、备份与旧数据

```powershell
uv run python evals/run.py
uv run python evals/run.py --live --limit 20 --max-calls 60
uv run python scripts/backup_db.py
uv run writing-web import-json data/revisions.json --username your_username
```

默认评测只跑演示生成器。`--live` 使用真实模型并计费；程序断言与人工语义判定分开，未评审的成功率不填数字。备份默认进入 `private/backups`。导入需先注册接收账户，保留原文件并去重，不会自动导入私人数据。

## 部署边界

Compose 面向单机部署。公网使用时，为本机8080入口配置域名与 HTTPS 反向代理，将 `WRITING_ORIGINS` 设置为实际 HTTPS 地址，并启用 `WRITING_SECURE_COOKIE=true`。持久卷不等于备份；恢复先在隔离数据库验收，定期演练。

正文上限20,000字符，候选输出最多4096 token，长正文可能因模型输出上限失败；失败会明确显示，不静默截断。最多保留最近4轮拒绝反馈，按64,000字符的上下文预算移除最旧反馈；这是明确的长度预算，不声称实现精确 token 分配。每任务最多3次执行，每次固定流程最多2次模型调用。供应商已经处理但连接超时的情况可能产生重复计费，不能保证外部调用恰好一次。

版本历史与变更见 [CHANGELOG](CHANGELOG.md)。旧命令行入口 `python main.py` 保留兼容，使用本地 JSON，不与网页数据库双向同步。

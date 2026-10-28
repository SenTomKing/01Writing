# 01Writing

一个带人工确认、版本历史和后台任务的 AI 写作工作台。用户在网页中保存正文、提出修改要求、比较候选，接受后形成新版本；拒绝原因会进入下一轮修改。历史恢复追加快照，不覆盖旧稿。

## v5.0：最后一个主要功能版本

现在可以从“证据不足／资料冲突”继续：填写补充说明、重新选择本次资料，建立关联的新修订任务。旧任务、原始依据和判断保持不变；新任务从当前选择重新快照。最多连续补充四轮，正文已被其他操作改动时拒绝过期提交。模型结果仍由你接受后更新正文。

- 文档首页增加账户统计；任务中展示请求编号、排队／执行／工具耗时、真实返回Token、重试及未确认响应调用。账户接受率只以明确接受和拒绝为分母，不是模型准确率；金额、搜索及Embedding账单没有混算。
- API、CI和容器共同使用`uv.lock`，前端使用pnpm冻结锁文件。容器以普通用户运行，保留原有数据库角色隔离。
- 提供升级前只读检查、隔离数据库备份恢复演练，以及默认只预览的孤立索引清理。索引保留历史任务引用，文档和任务不会被自动清理。
- 最终对照12题×3策略，原始输出、成本用量和失败分析见[v5最终验收](evals/v5.0.0-最终验收与项目边界.md)。不把接入成功、程序通过或演示模型当作真实语义准确率。

这是单机、人工确认的资料辅助写作产品，已覆盖主要功能路线。公网部署、OCR、多人协同编辑、多Agent、模型训练与自动长期学习均不在本项目交付范围；后续主要处理缺陷、依赖更新和评测回归。

## 延续的资料与Agent能力

在资料导入与核对基础上增加 pgvector、预算内证据窗口和逐项证据判断。上传 TXT、Markdown、文字 PDF，或者粘贴公开网页链接；系统提取正文、建立索引、保存来源快照，再供用户勾选用于修订。候选展示引用摘录及“资料支持／与资料矛盾／依据不足”核对结果，正文仍需人工接受后更新。

- 无额外服务密钥也能导入文件、抓取给定网页，使用关键词和中文双字匹配检索。网页不执行脚本，不绕过登录、付费墙或访问限制。
- 文件／网页最多2MB、正文100,000字符、PDF最多80页。PDF提取在Linux容器的受限子进程中运行；扫描件没有OCR，Windows原生开发模式不运行PDF提取。
- 导入走PostgreSQL任务＋Celery，支持进度、取消、有限恢复、同账户去重和索引重建；每账户最多2个活跃导入、每天30次、资料与记忆合计100项。
- 来源保存URL／文件名、抓取时间、哈希和PDF页码。修订保存所选资料快照；后续编辑或删除资料不改变旧任务依据。
- DeepSeek / Tavily 搜索：可直接复用官方 DeepSeek 密钥，也可配置 Tavily，并由用户逐任务勾选联网；Agent最多搜索2次、读取3页。搜索摘要只作线索，抓取的正文才作为引用依据。
- 可选兼容OpenAI接口的Embedding服务：需配置独立地址、模型和密钥；新导入或重建索引后，按关键词与向量排名融合检索。检索服务失败明确退回关键词模式。新任务的向量保存到 PostgreSQL pgvector 片段表，先限定账户和任务授权的不可变索引，再精确计算余弦相似度。旧JSON索引在新建任务时迁入，不额外请求Embedding；暂未启用HNSW近似索引。
- 独立证据核对最多检查12条陈述，核验摘录确实存在；它评估给定资料是否支持候选，不能保证资料本身真实，也不能替代人工审稿。失败明确显示未完成，不伪装为通过。
- 三种修订方式继续保留：直接生成、固定流程、Agent。Agent上限6次模型请求、48,000 token保守预算、单次执行120秒，独立核对也计入预算；不足时保留候选并提示未完成核对。

v4增加逐项证据判断：检索结果最多装配6个窗口、正文合计1800字符，保留原文位置与相邻限定语。证据不足可继续补查或询问；识别到资料冲突直接暂停，请用户确认。网页展示判断和实际检索记录。模型判断仍可能漏项或误判。

实际对照、失败样例及限制见 [v4量化验收](evals/v4.0.0-检索与证据判断验收.md)，历史 [v3验收](evals/v3.0.0-验收记录.md) 保留。当前只使用DeepSeek也能完成“导入→勾选→生成→核对→接受”；搜索和Embedding并非必填，网页会显示是否配置。

## 原有全栈功能

- 账户注册、登录、退出，文档、历史、任务、进度与导出按账户隔离。
- 文档创建、改标题、正文编辑保存、Markdown／TXT 导出。
- 后台生成、差异展示、人工接受、拒绝反馈与重新生成、取消和失败重试。
- 版本冲突检测、重复生成请求去重、重复接受不追加第二份版本。
- PostgreSQL 事务、关系约束、Alembic 迁移，执行租约、事件记录、有限恢复。
- React／TypeScript 网页，OpenAPI 生成的接口类型，SSE 与查询展示进度。
- 显式导入旧 JSON，保留源文件；基础 A／B 评测与数据库备份入口。

技术栈：Python、FastAPI、Pydantic、SQLAlchemy、PostgreSQL、Celery、Redis、React、TypeScript、Vite、Docker Compose。架构与边界见 [架构说明](docs/architecture.md)，评测方法见 [evals](evals/README.md)。Agent 使用 LangGraph；关键词检索默认可用，搜索与语义服务按需接入。本版不包含多Agent、OCR或自动长期学习。

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

`down` 保留数据库卷；不要使用 `down -v` 作为日常停止方式。容器初始化使用独立的 postgres 管理员；迁移使用 writing_owner，日常 API／任务使用 writing_app，两者均无超级用户权限。管理员密码只提供给数据库容器。已有数据卷不会再次运行初始化脚本，改环境变量不会自动重设旧库密码。升级前备份，更新代码后重新构建，迁移服务成功以后 API 才启动。

## 从 v3 升级已有数据卷

升级前先备份，确认没有运行中的修订或导入，再停止 API、worker、dispatcher。数据库镜像保持 PostgreSQL 17；不要换成比现有数据库更旧的次版本。已有数据卷不会重新执行初始化脚本，因此先更新 postgres 容器，再执行：

```powershell
docker compose --env-file .env.deploy exec -T postgres psql -U postgres -d writing -c "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;"
docker compose --env-file .env.deploy run --rm migrate
docker compose --env-file .env.deploy up -d --no-build
```

先构建好应用镜像后再安排短暂升级窗口。若 PostgreSQL 提示 collation version mismatch，先保持应用停止，由管理员执行 `REINDEX DATABASE writing;`，成功后再执行 `ALTER DATABASE writing REFRESH COLLATION VERSION;`。排序库变化时不能仅刷新版本号而不重建索引。账号、正文与历史保留；不可变索引保留旧任务依据，删除资料不会抹去已创建任务的快照。若迁移失败，停止升级并恢复旧应用；不要删除数据卷。孤立索引清理和保留边界见下方运维命令；正文与修订历史不自动过期。

## 可选搜索和语义服务

联网搜索默认可复用官方 DeepSeek 密钥；语义检索使用独立的 Embedding 服务。在服务端私有 `.env.deploy` 中按需添加，之后重新创建API、worker和dispatcher容器：

```dotenv
WRITING_SEARCH_PROVIDER=auto
TAVILY_API_KEY=
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_API_KEY=
```

搜索设为 `auto` 时优先已有 Tavily，否则在使用官方 DeepSeek 聊天服务时复用其密钥；也可指定 `deepseek`、`tavily` 或 `none`。只有用户勾选允许联网才调用搜索，不会因为配置了密钥就主动搜索。语义检索需要后三项同时有效；硅基流动密钥填入 `EMBEDDING_API_KEY`，它与 DeepSeek 密钥不同。新索引按段落、句末和PDF页边界切分，每块最多900字符，相邻块最多重叠120字符且不跨页。切分参数记录在索引中；没有测评数据时不宣称提升召回率。启用Embedding后，为已有资料点击“重建索引”；供应商、模型或地址改变后也需重建。索引构建失败会显示导入失败，不会把未完成的语义索引标成可用。查询语义服务失败则明确降级关键词。查询和资料片段会发送给对应供应商，费用按供应商计费；这些密钥永远不写入前端或Git。

## 本机开发：复用已安装的 PostgreSQL

建议 Python 3.13、Node.js 24、pnpm 10.20。本机 PostgreSQL 还须安装 pgvector 扩展文件，由管理员在目标库执行 `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;`；普通应用角色不需要超级用户权限。先准备 `writing` 数据库，迁移账户能建表，应用账户能读写；给迁移账户创建的新表和序列设置默认授权。连接参数放在 `.env.postgres.local`，模型参数可放在 `.env`，格式参考 `.env.example`。

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

Windows 已安装 Edge 时，可复用它运行浏览器验收，省去单独下载 Chromium（前端项目依赖仍需安装）：

```powershell
$env:WRITING_BROWSER_CHANNEL = "msedge"
$env:WRITING_WEB_URL = "http://127.0.0.1:8000"
node scripts/browser-smoke.mjs
```

验收会实际检查历史恢复后的正文、Markdown／TXT 下载内容，以及退出后重新登录能否重新读取同一文档。脚本创建独立随机测试账户，不使用你的个人账户。

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

### 检索质量实测

[检索切分与向量模型对照实验](evals/检索切分与向量模型对照实验.md)包含5种切分、两种Embedding模型及不同排序的55组对照，公开72题数据、3960条逐题结果、实际用量和离线复算工具。当前方案在自建留出集上前5块证据召回为100%，受1500正文字符预算限制时为76.56%；仅在开发集选出的300字关键词方案对应98.44%。这一结果说明切分、上下文预算与数据分布会影响结论，不代表关键词普遍胜过向量；生产默认参数尚未因此更改。

## v5 运维入口

```powershell
python deploy/operations.py preflight
# 没有活动任务后，安排短暂停用再演练。演练不会覆盖正式数据库。
docker compose --env-file .env.deploy stop api worker dispatcher
python deploy/operations.py restore-drill
docker compose --env-file .env.deploy up -d --no-build
```

恢复演练把实际备份恢复到随机新建的隔离数据库，逐表比较行数和内容哈希，最后只删除该临时数据库。备份和报告保存在`private/backups`。它验证同实例恢复，不代表异机灾备、域名或HTTPS已经配置；备份不含角色所有权和ACL，需用初始化脚本和`deploy/check-db.sql`另外验权。若演练出错，先检查备份和错误，再恢复原应用服务，不清空数据卷。

```powershell
docker compose --env-file .env.deploy exec api writing-web cleanup-indexes --username your_username
# 核对预览后才加 --apply；最多100个索引一批。
```

只清理至少7天前创建、来源资料已删除、且没有任何任务引用的索引。任务引用的旧原文长期保留；该命令不是账户全部数据删除接口。

v4→v5没有新增数据库结构迁移，沿用`v4_vectors`。升级仍先备份，并保存旧运行镜像ID及标签。新版本检查失败时用旧镜像重新启动应用，不能用旧备份直接覆盖用户新增数据。不要运行`down -v`。

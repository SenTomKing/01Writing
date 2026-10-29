# v6 个人模型配置与 Vercel 前端部署

v6 将真实模型消费改为每个账户自带 DeepSeek 与硅基流动密钥。Vercel 只放前端和短请求转发；PostgreSQL、Redis、Celery、模型执行仍在部署者电脑，不需要租服务器。公网连通尚须部署者启动 HTTPS 隧道并配置站点地址。

## 本机先可用

新部署先运行 `python deploy/prepare_env.py`。现有 v5 部署运行 `python deploy/prepare_personal_keys.py`，它只追加缺失的加密主密钥，不覆盖旧值。将 `.env.deploy` 的 `WRITING_PROVIDER` 设为 `deepseek`。

加密主密钥不是模型密钥。它用于解密数据库里的个人凭证，必须与数据库备份一起妥善保存，并保留独立副本。不要更改或丢失它；丢失后无法恢复旧凭证，只能由各用户重新录入。服务器管理员持有主密钥，因此这不是端到端加密，只应在可信部署上输入密钥。不要提交 `.env.deploy`、数据库备份或个人资料。

升级前等待任务完成，停止 API、worker、dispatcher；备份数据库并在隔离库恢复验证。之后串行构建，不删除数据卷：

```powershell
$env:COMPOSE_PARALLEL_LIMIT="1"
docker compose --env-file .env.deploy build api
docker compose --env-file .env.deploy build web
docker compose --env-file .env.deploy run --rm migrate
docker compose --env-file .env.deploy up -d --build
```

内存较小的电脑可设置 `COMPOSE_PARALLEL_LIMIT=1` 后执行。保留旧应用镜像及升级前备份，回退时一起核对迁移版本；不要将 `down -v` 用作停止命令。

打开 http://localhost:8080，注册或登录，进入右上角“模型设置”，同时保存两个个人 API 密钥。页面不读回原文，只显示已保存。可选连接检测会检查 DeepSeek 身份认证，并发送一次短文本 Embedding 请求，可能产生极少量费用；它不代表所有模型和搜索权限均验证通过。

个人服务固定为 DeepSeek `deepseek-flash` 和硅基流动 `BAAI/bge-m3`。旧 BGE-M3 索引可以继续使用；密钥本身更换不改变向量空间。不同账号的凭证不共享。任务创建时只保存凭证版本号，worker 执行前再次校验，更换／删除后排队任务失败，重新提交即可绑定新凭证。已经开始的调用可能仍会完成，供应商控制台吊销密钥才能阻止后续供应商使用。旧任务没有个人凭证绑定时也不会借用部署者密钥。

## Vercel 页面怎样填

1. 从 GitHub 导入此仓库，选择 **frontend / Import single project**，Root Directory 为 `frontend`，预设为 **Vite**；不要将整个 FastAPI 项目导入为多服务部署。
2. 保持仓库 `frontend/vercel.json` 中的构建设置，Node.js 选择24。无需粘贴本机 `.env.deploy`。
3. 先启动本地 Docker 服务，再运行安装好的 cloudflared：

```powershell
E:\Tools\cloudflared\cloudflared.exe tunnel --url http://localhost:8080
```

4. 复制该程序实际输出的 `https://随机名称.trycloudflare.com`，在 Vercel 的服务端环境变量添加 `WRITING_BACKEND_ORIGIN`，值只填此 HTTPS 来源地址，不加 `/api`。不要加 `VITE_` 前缀，不要在 Vercel 填个人模型密钥、数据库密码或加密主密钥。
5. 点击 **Deploy**，得到最终生产网址后，将本机 `.env.deploy` 的 `WRITING_ORIGINS` 改为 `http://localhost:8080,https://你的实际站点.vercel.app`。只添加你自己的确切域名，不使用任意通配域名。重新创建 API、worker 和 dispatcher 使环境生效：

```powershell
docker compose --env-file .env.deploy up -d --no-deps api worker dispatcher
```

6. 打开 Vercel 网址注册／登录，保存个人双密钥，检测连接，然后新建文档、生成、接受、刷新确认版本仍在。HTTP 本机使用可保持 `WRITING_SECURE_COOKIE=false`；Vercel 转发层强制将公开站点会话 Cookie 设为 Secure。若将后端直接作为 HTTPS 服务使用，则后端也应设 `WRITING_SECURE_COOKIE=true`。

Quick Tunnel 重启会产生新地址；需更新 Vercel 环境变量并重新部署。它用于临时试用，没有全天候可用性保证，而且不支持 SSE，因此 v6 网页以轮询读取任务进度。隧道运行时会使本机网页 API 可以从公网访问，避免把此地址当作安全口令。

## 数据位置和运行边界

- 用户、密码哈希、会话、加密 API 凭证、文档、版本、资料和 pgvector 向量，均保存在本机 Docker PostgreSQL 的 `postgres_data` 持久卷，不是 Vercel，也不是另一套 Windows 原生 PostgreSQL。实际卷名前缀由 Compose 项目名决定。
- Vercel 转发请求时会处理 Cookie、正文和用户输入的密钥；代码不将它们写入日志或缓存。浏览器只请求同源 `/api/v1/...`。数据库端口不通过隧道开放。
- Vercel 页面可以继续打开，但电脑关机、睡眠、Docker 退出或隧道停止时，登录和业务操作不可用。Docker 窗口可关闭，Docker 引擎不能退出。
- 这是可信小范围试用方案。没有邮件验证、找回密码、邀请码或运营风控，现有限流在单机代理环境下可能被多个用户共享；不承诺高并发、永续免费或生产 SLA。
- Demo 模式不调用真实服务，无需个人密钥，页面明确显示离线演示。它不等于真实模型验收。

## 验证范围

v6 使用真实隔离 PostgreSQL 验证加密落盘、账号绑定、CSRF、版本失效和 worker 凭证选择；供应商用假实现，不重复消费 v5 评测额度。代理离线测试覆盖路由、Cookie、CSRF、上传限制、断线、重定向和不缓存。公网端到端连通必须在实际隧道及 Vercel 网址配置后另行确认，不能用本机测试替代。

参考：[Vercel Node.js Functions](https://vercel.com/docs/functions/runtimes/node-js)、[Vercel Rewrites](https://vercel.com/docs/routing/rewrites)、[Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)。

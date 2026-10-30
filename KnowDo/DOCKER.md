# Docker Compose 部署

Compose 环境提供独立的 `api`、`db` 两个服务。Windows 访问 API 用 `127.0.0.1:8001`，访问 PostgreSQL 用 `127.0.0.1:15432`；`api` 容器访问数据库用 `db:5432`。服务名解析和端口含义见 [Docker Compose 网络文档](https://docs.docker.com/compose/how-tos/networking/)。

## 端口与网络

`compose.yaml` 的 `ports` 按 `主机 IP:Windows 端口:容器内端口` 书写。这里有两个不同程序：Uvicorn 提供 HTTP API，PostgreSQL 提供数据库服务。

```text
Windows 浏览器/curl → 127.0.0.1:8001 → api 容器的 8000（HTTP）
Windows psql/数据库客户端 → 127.0.0.1:15432 → db 容器的 5432（PostgreSQL）
api 容器 → db:5432（同一 Compose 网络内直接访问数据库服务）
```

`api.command` 用 `uvicorn ... --port 8000` 指定 API 容器内的监听端口；`api.ports` 把 Windows 的 `8001` 映射过去。`db` 使用 `pgvector/pgvector:pg17` 镜像，在容器内监听 `5432`；`db.ports` 把 Windows 的 `15432` 映射过去。`api.environment.DATABASE_URL` 中的 `@db:5432` 告诉应用向哪个数据库发起连接。`Dockerfile` 也有 Uvicorn 的默认 `CMD`，但当前 Compose 的 `command` 会覆盖它。`web/api.py` 定义的是应用和路由，不负责设置 Uvicorn 或 PostgreSQL 的监听端口。

## 第一次准备

在 PowerShell 进入项目目录：

```powershell
cd D:\dev-learning\KnowDo
if (-not (Test-Path .env)) { Copy-Item .env.compose.example .env }
```

如果是刚复制的 `.env`，把两个占位值改成不同的随机字符串。可运行两次 `python -c "import secrets; print(secrets.token_hex(32))"` 生成；数据库密码使用生成的十六进制字符串（只含字母和数字），不要包含 URL 特殊字符。`.env` 已被 Git 忽略，`.dockerignore` 也不会把它放进镜像。不要把值提交到 Git。应用代码只读进程环境变量；Compose 读取 `.env` 后通过 `environment` 显式传给容器。参考 [Docker Compose 环境变量文档](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)。

执行：

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 api
```

`config --quiet` 只检查解析是否成功，不打印包含凭据的完整配置。`up -d --build` 构建 API 镜像并启动两个服务；`db` 健康后，`api` 先运行 `alembic upgrade head`，成功后再启动 Uvicorn。`ps` 看服务状态和端口；`logs` 看迁移/启动报错。构建首次需要拉取 Python 基础镜像，网络较慢时会等待。

浏览器打开 `http://127.0.0.1:8001/docs`。先注册一个新账号，再登录并通过 Authorize 填入访问令牌；创建任务并查询列表。也可以先执行 `curl.exe -i http://127.0.0.1:8001/tasks`，未登录预期得到 401，而不是连接失败。

如果不使用 Compose，而是在本地启动 SQLite API，必须告诉 Uvicorn `web/api.py` 所在目录：

```powershell
..\.venv\Scripts\python.exe -m uvicorn --app-dir KnowDo api:app --reload
```

## PostgreSQL 集成测试

`tests/test_postgres_integration.py` 只接受独立数据库 `study_tasks_test`，并要求 PostgreSQL 已安装 pgvector。Compose 默认创建的是 `study_tasks`，首次测试前创建独立测试库：

```powershell
docker compose exec db psql -U study -d study_tasks -c "CREATE DATABASE study_tasks_test"
$env:TEST_DATABASE_URL = 'postgresql+psycopg://study:密码@127.0.0.1:15432/study_tasks_test'
..\.venv\Scripts\python.exe -m pytest -q tests/test_postgres_integration.py
```

## 观察与排错

```powershell
docker compose ps
docker compose logs --tail=80 db
docker compose logs --tail=80 api
docker compose exec db psql -U study -d study_tasks -c "SELECT version_num FROM alembic_version;"
```

当前迁移版本是 `0017_agent_message_sequence`。若 API 容器不能连接数据库，先看 `db` 是否 healthy，再看 `api` 日志里的错误属于 DNS、TCP、账号密码还是迁移。不要在日志中打印 `DATABASE_URL` 或令牌。命令选项 `-U` 必须使用普通的 ASCII 短横线；`—U` 是长破折号，会被 `psql` 当成用户名的一部分。

PostgreSQL 日志中首次初始化时的临时启动、关闭、再正式启动是正常过程；结合最终的 `database system is ready to accept connections` 和 `docker compose ps` 的 `healthy` 判断当前状态。API 日志中的 HTTP 401 表示请求已经到达应用，只是没有有效的登录令牌。

### 连接故障：DNS、端口与认证

在项目目录执行下面三条命令，从 `api` 容器发起 DNS 查询或 TCP 连接。它们只读、不修改数据库数据；第一条（域名解析错误）与第二条（连接被拒绝）预期以非零状态退出，第三条成功。

```powershell
docker compose exec -T api python -c "import socket; socket.getaddrinfo('db-wrong', 5432)"
docker compose exec -T api python -c "import socket; socket.create_connection(('db', 55432), timeout=3)"
docker compose exec -T api python -c "import socket; s=socket.create_connection(('db', 5432), timeout=3); print('TCP connected to', s.getpeername()); s.close()"
```

三条命令分别区分：服务名找不到（DNS）、服务名存在但目标端口没有程序监听（TCP）、正确地址和端口可连接。打印的容器 IP 由 Docker 分配，不要写死到配置中；应用连接数据库仍使用 `db:5432`。再与 `curl.exe -i http://127.0.0.1:8001/tasks` 的 401 对比：401 是 HTTP 层已经收到并处理请求后的响应。

```powershell
docker compose restart api
docker compose ps
docker compose down
```

`restart api` 只重启已有的 API 服务容器，可用于观察应用重启后数据是否仍在；`down` 停止并移除本套 Compose 的容器和网络，**保留**命名卷（`postgres_data`、`fastembed_cache`）中的数据库数据。执行 `down` 后没有容器供 `restart` 重启；使用 `docker compose up -d --build` 重新创建并启动服务。不要使用 `docker compose down -v`，它会删除本套环境的数据卷。

已创建数据卷后，不要只修改 `.env` 中的数据库密码：PostgreSQL 容器首次初始化时设置的旧密码仍在库中，新连接可能认证失败。

## Agent 与 RAG 运行时

Compose 默认设置 `AGENT_RAG_ENABLED=1`，认证后的 `POST /agent/chat` 可以使用 `search_knowledge`。生产链路是：JWT 得到可信 `owner_id`，关键词和 pgvector 两侧都在 SQL 阶段筛选该用户且处于 `ready/active` 的分块，RRF 融合后作为 tool message 回传给 DeepSeek。

FastEmbed 模型在第一次知识检索时延迟加载，向量维度必须等于 `EMBEDDING_DIMENSION=512`。模型文件保存在 `fastembed_cache` 命名卷中，重建 API 容器不会重复下载。若 PostgreSQL/pgvector 不可用、模型名或维度不匹配，`/agent/chat` 返回 503；DeepSeek 上游请求失败返回 502。

认证用户可以通过 `POST /documents` 上传纯文本资料，也可以通过 `POST /documents/file` 上传 Markdown、TXT 或文本型 PDF。相同 `source` 使用新的 `document_version` 表示重建；相同版本和相同内容重复请求是幂等操作，相同版本但内容不同返回 409。`GET /documents`、`GET /documents/{document_id}` 和 `DELETE /documents/{document_id}` 都使用 JWT 中的 owner_id，不接受客户端提供 owner_id。

上传成功后文档状态为 `ready`，即可通过 `/agent/chat` 的 `search_knowledge` 检索。删除会在一个事务中标记生命周期状态并删除对应版本的向量 chunks。

### 结构化日志

API 响应包含服务器生成的 `X-Request-ID`。使用该值可以在容器日志中关联 HTTP、Agent、模型请求和知识检索事件：

```powershell
docker compose logs --no-color api | Select-String "这里替换为X-Request-ID"
```

日志是单行 JSON 元数据，包含 `agent_run_id`、step、tool_call_id、duration_ms、candidate_count、token 和状态。日志不会记录 JWT、API Key、问题正文、工具参数或文档内容。可用 `STUDY_TASK_LOG_LEVEL` 调整 `study_task.*` 日志级别，默认是 `INFO`。

## CI

仓库根目录的 GitHub Actions 工作流在推送和拉取请求时安装 `requirements.txt`，然后从 `KnowDo/` 目录运行 `python -m pytest -q`。这组测试使用临时 SQLite 文件，不需要真实密钥；只有设置了 `TEST_DATABASE_URL` 才会运行 PostgreSQL 集成测试。

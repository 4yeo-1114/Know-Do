# 目录职责与请求链路

根目录 `api.py` 是 Uvicorn 入口，只导出应用工厂和应用对象；实际路由在 `web/api.py`。

| 目录 | 主要模块 | 查找内容 |
|---|---|---|
| `web/` | `api.py`, `auth.py`, `schemas.py` | HTTP 路由、认证依赖、请求与响应结构、错误映射 |
| `agent/` | `agent_loop.py`, `readonly_tools.py`, `knowledge_tools.py`, `planning_tools.py`, `plan_approval.py`, `session_store.py`, `persistent_runtime.py` | 模型决策循环、工具参数校验、预算、工具结果回传、会话持久化、审批图 |
| `llm/` | `deepseek_api.py`, `deepseek_client.py`, `deepseek_tools.py`, `model_client.py` | 模型配置、HTTP/SSE 协议、工具 schema、响应解析 |
| `tasks/` | `service.py`, `repository.py`, `task_proposals.py` | 任务业务、任务查询、提案确认、事务与幂等 |
| `fitness/` | `service.py`, `repository.py`, `importer.py` | 训练/体重记录、聚合统计、CSV 两阶段导入 |
| `user_profile/` | `service.py`, `repository.py` | 显式档案与 revision 乐观并发 |
| `weekly_plans/` | `service.py` | 周计划提案、版本校验、确认/拒绝/调整 |
| `knowledge/` | `document_service.py`, `document_ingestion.py`, `document_lifecycle.py`, `pdf_parser.py` | 文档导入、切块、版本状态、激活、删除、PDF 文本解析 |
| `knowledge/` | `embedding_retrieval.py`, `keyword_retrieval.py`, `hybrid_retrieval.py`, `retrieval_strategy.py` | 向量化、关键词全文检索、融合排序 |
| `knowledge/` | `knowledge_runtime.py`, `rag.py`, `rag_grounding.py`, `retrieval_eval.py`, `formal_dataset.py`, `retrieval_baseline.py`, `retrieval_scale.py` | 运行时组装、上下文、证据约束、检索评测与数据集 |
| `storage/` | `database.py`, `models.py`, `pgvector_store.py`, `migration.py`, `legacy_sqlite_migration.py` | 连接池、Session、表定义、向量 SQL、迁移辅助 |
| `core/` | `observability.py`, `reliability.py` | 请求 ID、日志输出、错误重试策略 |
| `migrations/` | `versions/` | Alembic 迁移（当前 `0017_agent_message_sequence`） |
| `legacy/` | `main.py` | 早期 CLI 与 SQLite 原型实现 |
| `frontend/` | `index.html`, `app.css`, `app.js` | 六视图浏览器工作台 |

## 请求链路

```text
api.py -> web/api.py
            -> web/auth.py
            -> agent/agent_loop.py -> llm/deepseek_tools.py
                    -> agent/readonly_tools.py -> tasks/service.py
                    -> agent/knowledge_tools.py -> knowledge/hybrid_retrieval.py
                    -> agent/planning_tools.py -> weekly_plans/
                    -> tasks/task_proposals.py
            -> knowledge/document_service.py -> knowledge/document_ingestion.py
            -> fitness/、user_profile/、weekly_plans/
业务服务 -> storage/ 的 ORM、Session 和 SQL
日志与重试 -> core/
```

`web/api.py` 集中组装路由；`knowledge/` 不再继续嵌套，按文件名定位。审批类流程（任务提案确认、周计划确认）由 `agent/plan_approval.py` 与 `agent/persistent_runtime.py` 提供 LangGraph checkpoint 持久化与 interrupt/resume。

## 启动与导入

在仓库根目录启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn --app-dir KnowDo api:app --reload
.\.venv\Scripts\python.exe -m pytest -q
```

在 `KnowDo/` 中启动：

```powershell
python -m uvicorn api:app --reload
python -m legacy.main
```

导入使用包路径，例如 `from storage.database import Database`；不要直接运行包内文件（如 `python agent/agent_loop.py`）。Alembic 从 `storage.models` 读取模型，迁移文件位于 `migrations/`。Dockerfile 显式复制生产包和前端。

# 项目质量与失败复盘

## 报告范围

本报告总结当前实现的 Agent、RAG、文档生命周期、任务提案与 Compose 部署能力，记录验证证据、真实故障与已知限制。它描述的是单体应用的验证结果，不是生产 SLA 或大规模容量报告。

## 验证基线

| 项目 | 证据 |
|---|---|
| 自动化回归（SQLite） | 357 项收集，0 失败（10 跳过） |
| PostgreSQL 专项 | 356 passed / 1 skipped（真实 PostgreSQL + pgvector） |
| 浏览器端到端 | 8 场景 76/76，page_errors=0（Playwright + 真实 Edge，离线桩模型） |
| 确定性行为场景 | 79 项全部通过（离线桩模型） |
| CSV 幂等探针 | 10/10 轮，业务数据恰好一份（双线程 + 真实 PostgreSQL） |
| Compose | `db` healthy，`api` running，迁移版本 `0017_agent_message_sequence` |
| 静态前端 | `GET /app/` 返回 200 |
| 未认证保护 | `GET /tasks` 返回 401 |
| 文档入库 | 临时资料上传后为 `ready`，生成 1 个 chunk |
| RAG 在线链路 | 模型步骤、知识工具调用、来源引用返回 200 |
| 无证据保护 | 空检索结果时 Agent 明确拒答 |
| 写入确认 | 确认前任务数 0，确认后任务数 1 |
| 幂等 | 同一提案重复确认返回已有 confirmed 结果，没有第二条任务 |

以上数字为 2026-10-23 仓库 head 的本地实测，原始证据保存在 `benchmarks/` 下。

## 失败分层

### 认证层

触发：缺少、过期或签名错误的 JWT。

处理：`api.py` 的 `current_user_id()` 调用 `auth.user_id_for_token()`，失败时返回 401。`user_id` 不从请求 body、前端字段或模型参数读取。

影响：请求在进入任务、文档或 Agent 业务前结束，不会执行用户数据查询。

### 工具协议层

触发：模型返回未知工具名、缺少工具参数、参数超出 schema，或者重复的 `tool_call_id`。

处理：`agent_loop.py` 先进入 `VALIDATING`，再执行白名单和 Pydantic 校验；`api.py` 映射为 422。未知工具不会触发本地函数和数据库操作。

### 模型网络和上游层

触发：连接失败、读取超时、DeepSeek 返回 429/503 或其他非 2xx。

处理：`deepseek_api.py` 将网络异常和 HTTP 错误分类；`reliability.py` 的 `RetryPolicy` 只对模型请求、只读工具和幂等写入允许有限退避重试。401 等认证错误不重试，非幂等写入不放进 HTTP 重试器。

对外结果：上游模型失败通常映射为 502；本地模型配置缺失或 RAG 运行时不可用映射为 503。

### Agent 资源层

触发：模型步骤数、工具调用次数或累计 Token 超过 `AgentBudget`。

处理：`agent_loop.py` 在执行整批工具之前预检查预算。预算不足时整批不执行，避免部分工具产生副作用；API 返回 429。

### 检索层

触发：资料没有命中、召回了错误分块、或候选质量不足。

处理：`retrieval_eval.py` 记录 Top-1、Top-K、无答案正确率、候选数量和耗时；`diagnose_grounding_case()` 把问题归类为 `retrieval`、`generation` 或 `citation`。`owner_id`、`ready` 和 `active_version` 在 SQL 阶段过滤。

无资料时，`rag_grounding.py` 和 Agent system 约束要求明确拒答，不能让模型绕过资料库使用自身知识。

### 文档生命周期层

触发：切块、Embedding、向量写入或版本激活失败。

处理：`document_ingestion.py` 将当前版本标为 `failed`，保留旧的 `ready/active` 版本；只有完整写入后才激活新版本。删除操作由 `DocumentLifecycle.delete()` 在同一事务中处理生命周期和 chunks。

### 写入业务层

触发：模型重复提出创建任务、用户重复确认、提案过期、跨用户确认或并发状态冲突。

处理：`task_proposals.py` 使用 owner 作用域的幂等键、状态条件更新、过期检查和事务。模型只创建 `pending proposal`，确认 API 才能创建 Task。重复确认返回已确认结果，不重复写入。

## 一次真实故障定位示例

曾出现系统日志中看不到业务 JSON 事件的问题。根因不是 Agent 或数据库，而是应用 logger 没有有效 handler，且没有复用 Uvicorn 的父 logger handler。

修复位置：`observability.py` 的 `configure_application_logging()`：

1. 优先查找 `uvicorn.error.handlers`；
2. 没有时查找 `uvicorn.handlers`；
3. 将 handler 绑定到 `study_task` logger；
4. 设置 `propagate=False`，避免重复输出。

修复后的真实请求可以按 `X-Request-ID` 关联 HTTP、Agent、模型和检索日志。例如一次资料问答记录了：

```text
HTTP 总耗时：       2029.727 ms
模型 step 1：       1311.100 ms
知识检索：           15.665 ms / 8.543 ms
模型 step 2：        658.214 ms
```

这说明该请求的主要耗时来自模型阶段，而不是 PostgreSQL 检索。

## 已知限制

- 资料解析支持纯文本与文本型 PDF；扫描件/加密 PDF 返回 415，暂不做 OCR，无 Word 解析。
- 检索评测基于合成语料与独立实验库，未做大规模容量压测。
- 浏览器 E2E 使用离线桩模型，在线回答质量与费用不在 E2E 验收范围。
- 目前是单体 FastAPI 部署，没有异步队列、横向扩展或分布式 tracing。

## 质量结论

当前项目具备完整的 Agent 闭环：认证、用户隔离、RAG、工具白名单、预算、写入确认、幂等、文档版本、结构化日志和 Docker 启动都有代码和验证证据。项目最重要的质量策略是把模型的不确定性限制在决策层，把身份、权限、事务和资源边界留在服务端。

它不应被描述成高并发生产系统；更准确的项目表述是：一个经过真实 Compose 联调、带安全边界和失败分层的单体 Agent/RAG 应用。

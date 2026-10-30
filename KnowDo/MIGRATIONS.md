# 数据库表结构迁移（Alembic）

项目使用 Alembic 管理表结构。应用启动时只创建 Engine 和 Session factory，不自动建表；先执行迁移，再启动服务。迁移文件位于 `migrations/versions/`，当前版本为 `0017_agent_message_sequence`。

## 执行迁移

在仓库根目录的 PowerShell 中，安装依赖后执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r KnowDo\requirements.txt
.\.venv\Scripts\python.exe -m alembic -c KnowDo\alembic.ini upgrade head
.\.venv\Scripts\python.exe -m alembic -c KnowDo\alembic.ini current
```

没有设置 `DATABASE_URL` 时，上述命令操作 `KnowDo/tasks.db`。若要迁移 PostgreSQL，先在当前终端设置 `DATABASE_URL` 为目标数据库连接串，再执行相同的 `upgrade head` 和 `current`。不要把真实密码写进文档或提交到 Git。

`upgrade head` 按版本文件实际修改表结构，并在 `alembic_version` 记录已执行版本；`current` 只读取当前版本。`stamp` 只登记版本，不建表也不改表，只有确认已有表结构与对应版本文件一致时才能使用。

## 版本演进

| 版本 | 内容 |
|---|---|
| `0001` | 初始任务表 |
| `0002` | 用户表与任务归属（owner_id） |
| `0003` / `0004` | 任务提案表与提案历史保留 |
| `0005` / `0006` | pgvector `document_chunks` 表与 HNSW 索引 |
| `0007` | 文档生命周期状态 |
| `0008` | 提案幂等键 |
| `0009` / `0010` / `0011` | 任务活动字段、训练记录、CSV 导入 |
| `0012` | 关键词全文检索（tsvector + GIN）与来源元数据 |
| `0013` | 学习档案（user_profiles） |
| `0014` | Agent 会话持久化 |
| `0015` / `0016` | 周计划与调整提案 |
| `0017` | 会话消息序号 |

升级到 `0012` 时，已有 `document_chunks` 的 `search_tokens` 默认为空，迁移不会自动对旧正文分词。已有资料需要使用新的 `document_version` 重新入库并确认其状态为 `ready`，关键词全文检索才会包含新版本；原活动版本在重建失败时仍可保留。此限制不影响向量检索。

## 旧库迁移

早期 CLI 阶段未使用 Alembic 的旧 `tasks.db` 可用 `storage/legacy_sqlite_migration.py` 一次性迁移。脚本会拒绝覆盖已有备份或重复运行；迁移前先停止使用该数据库的程序。

## 结构检查

`alembic check` 的自动结构比较只包含项目模型管理的表；同库中不属于本项目的表不会被建议删除。

# 任务提案与确认（写入闸门）

模型只能**建议**调用 `create_task`，不能直接写库。应用先检查工具名和参数，把标题、优先级、截止日期存为绑定当前用户的 `pending` 提案；此时 `tasks` 表不变，提案有效期为 15 分钟。只有持有该用户有效 JWT 的确认请求才能创建任务。

## 数据流

```text
用户描述 → DeepSeek 返回 create_task(arguments)
         → Pydantic 拒绝空标题、非法优先级、模型提供的 user_id/owner_id
         → task_proposals 新增 pending 记录（随机提案 ID）
         → 向用户展示待创建内容
         → 用户确认 → 同一事务中将提案改为 confirmed 并插入 tasks
                     → 重复确认只读取原 task_id，不再插入
         → 用户拒绝 → 状态变为 rejected，之后不能确认
```

用户身份由登录后的服务端代码提供，不从模型的 `arguments` 中取。模型的 `call_id` 用于关联模型消息；应用生成的提案 ID 用于后续确认与幂等，两者职责不同。别人即使猜到提案 ID，也不能读取、确认或拒绝它。过期提案不能确认；若已确认的任务后来被删除，重复确认仍不会重新创建。

## API

| 路由 | 作用 |
| --- | --- |
| `POST /agent/task-proposals` | 将自然语言交给模型，返回待确认提案；不写 `tasks` |
| `GET /agent/task-proposals/{id}` | 仅所属用户查看提案 |
| `POST /agent/task-proposals/{id}/confirm` | 确认；重复调用返回同一任务，不重复创建 |
| `POST /agent/task-proposals/{id}/reject` | 拒绝待确认提案 |

未登录返回 401；其他用户的提案返回 404；已拒绝的提案确认返回 409；过期确认返回 410。网络重试或用户重复点击确认，都不会凭同一提案创建第二条任务。

周计划采用同一条提案-确认路径，且确认条件更严格：还校验档案 `revision` 与计划 `plan_version`，旧提案不能覆盖新事实。

# 测试用例设计

版本：v1.0 · 状态：Draft · 最后更新：2026-08-09

## 0. 测试策略

| 层级 | 目标 | 工具 | 是否连真实服务 |
|---|---|---|---|
| 单元测试 | provider / schema / state_machine / security / rag / idempotency | pytest | 否 |
| 集成测试 | 数据库模型 + 约束 + 级联删除 | pytest + test DB | 是（`*_test` 库） |
| API 测试 | 全部端点：认证 / 角色 / 状态码 / 幂等 | TestClient + fake providers | 否（内存 fake） |
| 任务测试 | RQ 任务逻辑（job_id / 重试 / 状态） | fakeredis + Fake Provider | 否 |
| 端到端（手动） | Docker Compose 全链路 + 真实 provider | 浏览器 + Swagger | 是（手工验收） |

**规则**：

- 测试默认使用 **Fake Provider**，强制 `CHAT_PROVIDER=fake`、`EMBEDDING_PROVIDER=fake`（conftest 内覆盖 env）；
- 测试不依赖网络、不依赖真实 API Key；
- 数据库用 `.env` 的 `DATABASE_URL` 派生 `*_test` 库（复用项目一 conftest 模式）；
- RQ 测试用 `fakeredis`，避免启动真实 Redis。

---

## 1. 单元测试

### 1.1 chat_provider

| 用例 | 断言 |
|---|---|
| FakeChatProvider 返回固定合法回复 | 输出非空、含"模拟" |
| Fake 空 system_prompt 抛错 | `ChatProviderError` |
| OpenAICompatible 缺 env 配置抛错 | `ChatProviderConfigError` 列出缺失项 |
| OpenAICompatible 用 MockTransport 校验 payload | 请求 URL、headers 含 Bearer、model 正确 |
| 超时 / HTTP 错误 / 非法 JSON → 统一 ChatProviderError | 错误信息稳定 |
| `extract` 返回合法 schema | Fake 返回的 JSON 能过 Pydantic |

### 1.2 embedding_provider

| 用例 | 断言 |
|---|---|
| FakeEmbeddingProvider 输出 1536 维 | 维度正确 |
| 输入空串抛 `EmbeddingInputError` | 报错 |
| 输出数量不匹配抛 `EmbeddingOutputError` | 报错 |

### 1.3 ai_structured_output（schema）

| 用例 | 断言 |
|---|---|
| `TicketAnalysis.model_validate(合法 JSON)` 成功 | 各字段解析正确 |
| 缺 `category` / 非法 `priority` / `confidence > 1` | `ValidationError` |
| 缺失字段 / 多余字段处理 | extra 默认 ignore（或配置 forbid，二选一，建议 forbid 更严） |
| JSON 解析失败 → `StructuredOutputError` | 异常类型 |

### 1.4 state_machine

| 用例 | 断言 |
|---|---|
| 全量合法转移矩阵 | 每条转移产出预期新状态 |
| `open→close` / `replied→cancel` / 终态任何事件 | `InvalidTransitionError` |
| `allowed_events` 计算 | open 返回 `[start_review, cancel]` |
| 事件角色校验 | `cancel` 在 `in_review` 禁止 customer |

### 1.5 security

| 用例 | 断言 |
|---|---|
| bcrypt hash 不等于明文 / verify 通过 | 正确 |
| 签发 JWT → 解析回 user_id/role/exp | 正确 |
| 过期 / 篡改 token → 拒绝 | `401` |
| `require_roles` 角色不符 | `403` |

### 1.6 idempotency

| 用例 | 断言 |
|---|---|
| 同 key 同 body 重复请求 | 返回首次快照，只建 1 单 |
| 同 key 不同 body | `409 conflict` |
| 不传 key | 每次都新建 |
| 事务失败后重试 | 幂等键回滚，可重试成功 |

### 1.7 rag / vector_search

| 用例 | 断言 |
|---|---|
| 检索只返回 `ready` 文档 | 过滤正确 |
| 无匹配 → 空结果 | 前端提示"无知识库依据" |
| 相似度降序 | 排序正确 |

---

## 2. 集成测试（数据库）

| 用例 | 断言 |
|---|---|
| `users.email` 唯一约束 | 重复插入抛 IntegrityError |
| `tickets.status` CHECK 拦截非法值 | 抛 IntegrityError |
| 删除 knowledge_item 级联删 chunks | chunk 计数归零 |
| 删除 ticket 级联删 replies/evaluations/jobs | 关联计数归零 |
| 评价唯一约束 `(ticket_id)` | 二次评价抛 IntegrityError |
| `ai_processing_jobs` 唯一约束 `(ticket_id, job_type)` | 重复插入被拒 |

---

## 3. API 测试（TestClient）

### 3.1 认证

| 用例 | 断言 |
|---|---|
| 未带 token 访问受保护接口 | `401` |
| 注册 customer → 登录 → 拿 token | 流程通过 |
| 错误密码登录 | `401` |
| 停用账号登录 | `403` |

### 3.2 角色（RBAC）

| 用例 | 断言 |
|---|---|
| customer 调用 `POST /admin/users` | `403` |
| customer 调用 `POST /tickets/{id}/reply-suggestion` | `403` |
| customer 查看他人工单 | `403` |
| agent 创建工单走 `/admin/tickets` | `201` |
| agent 查看审计日志 | `403` |

### 3.3 工单

| 用例 | 断言 |
|---|---|
| customer 创建工单（带 Idempotency-Key） | `201` + 工单创建 |
| 同 key 重发 | `200` + 同 id + 总数不变 |
| 同 key 不同 body | `409` |
| 非法 title/description | `400` |
| 列表分页 | total/pages 正确 |
| 状态筛选 | 只返回符合项 |
| 详情含 ai_job | `status` 反映任务状态 |

### 3.4 状态机

| 用例 | 断言 |
|---|---|
| `open → in_review`（agent） | `200` + status |
| `open → close` | `409 invalid_state_transition` + allowed_events |
| `in_review → replied`（先发送回复） | `200` |
| `replied → closed` | `200` |
| `closed → 任意` | `409` |
| customer 触发 `start_review` | `403` |

### 3.5 AI 分析与回复

| 用例 | 断言 |
|---|---|
| 创建工单后 `GET /analysis` 返回 fake 分析 | 4 字段合法 |
| `POST /reply-suggestion`（agent） | `202` + job pending |
| 轮询详情直到 succeeded | 出现 `is_ai_suggestion=true` 草稿 |
| customer 触发 reply-suggestion | `403` |
| 知识库为空时生成建议 | 建议草稿内容为空 / 提示无依据 |
| AI 失败后 RQ 重试 | `ai_processing_jobs.attempts` 递增 |

### 3.6 回复与评价

| 用例 | 断言 |
|---|---|
| 审核草稿 PATCH | `200` + is_ai_suggestion=false |
| 已发送草稿再编辑 | `409` |
| 发送回复 `in_review→replied` | `200` + 审计 `reply.sent` |
| 未 in_review 直接发送 | `409` |
| customer 评价 closed 工单 | `201` |
| 二次评价 | `409` |
| 评价非 closed（如 open/replied/canceled） | `409 invalid_state_transition` |
| 非本人评价 | `403` |

### 3.7 知识库

| 用例 | 断言 |
|---|---|
| 上传 txt | `201` + processing →（任务后）ready |
| 上传 pdf | `201` |
| 上传非允许类型 | `400` |
| 上传超 20MB | `400` |
| customer 上传 | `403` |
| `POST /knowledge/search` | 返回相似度降序 |
| 删除文档级联 | `204` + chunk 归零 |

### 3.8 审计

| 用例 | 断言 |
|---|---|
| 状态转移后存在 `ticket.status_changed` 日志 | 数量 + 字段正确 |
| 发送回复后存在 `reply.sent` | actor 正确 |
| 删除知识库后存在 `knowledge.deleted` | entity 正确 |
| admin 查询分页 | total 正确 |
| 非 admin 查询 | `403` |

---

## 4. RQ 任务测试（fakeredis）

| 用例 | 断言 |
|---|---|
| `enqueue_ticket_analysis` 稳定 job_id | job_id = `ticket_analysis:{ticket_id}` |
| 重复入队返回同一 job | 不重复 |
| worker 执行成功后 `ai_processing_jobs` succeeded | result 落库 |
| 任务抛异常 → 重试计数 | attempts=2 → 失败置 failed |
| `cancel` 后队列任务取消 | job status canceled |

---

## 5. 端到端（手动验收，非 CI）

**前置**：`docker compose up` 三服务 + Redis + worker；`.env` 配置真实 Provider（手工验收阶段）。

**验收脚本**：

```
1. 注册 customer → 登录 → 拿 token
2. 创建工单（带 Idempotency-Key）
3. 轮询 GET /tickets/{id} 直到 ai_job.succeeded
4. admin 创建 agent 账号
5. agent 登录 → 打开工单 → start_review
6. 上传知识库 txt → 等 ready
7. 生成回复建议 → 等 succeeded
8. 编辑建议 → 发送 → 关闭
9. customer 登录 → 评价
10. admin 查看审计日志，确认 6 条关键操作均在
11. 重复 POST /tickets（同 key）确认不重复建单
```

---

## 6. 测试数量目标

| 模块 | 目标用例数 |
|---|---|
| chat/embedding provider | 12 |
| AI 结构化输出 | 8 |
| state machine | 10 |
| security/auth | 8 |
| idempotency | 6 |
| 工单 API（含 RBAC） | 18 |
| 回复/评价 API | 14 |
| 知识库/RAG API | 12 |
| 审计 API | 6 |
| RQ 任务 | 8 |
| 集成（数据库约束/级联） | 8 |
| **合计** | **110+** |

> 参考：项目一 122 个测试全绿。本项目目标 **≥110**，同样 0 网络依赖，仅保留 1 个弃用警告的容忍度。

---

## 7. 每个 PR 的测试门槛

1. `pytest -q` 全绿；
2. `git diff --check` 无空白错误；
3. 新功能必须带 ≥1 个覆盖用例；
4. 涉及权限的改动必须有正/反两个用例（允许 + 403）；
5. 涉及状态机的改动必须覆盖非法转移。

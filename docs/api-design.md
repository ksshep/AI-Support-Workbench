# API 设计

版本：v1.1 · 状态：Draft · 最后更新：2026-08-09

> 每个接口统一按以下字段描述：HTTP 方法 / URL / 是否需要登录 / 允许角色 / 请求参数 / 请求体 / 返回结构 / 错误码 / 幂等要求 / 是否产生后台任务。
> 全部接口前缀 `/api`。所有返回字段不含内部敏感信息。

---

## 0. 通用约定

- Base URL：`/api`。
- 认证：除 `/health`、`/auth/login`、`/auth/register` 外全部需要 `Authorization: Bearer <JWT>`。
- 分页响应统一：`{ items, total, page, page_size, pages }`。
- 错误统一 `{ "detail": { "code": ..., "message": ... } }`。
- 角色缩写：`all` = customer + agent + admin；`staff` = agent + admin。

### 错误码速查

| HTTP | code | 场景 |
|---|---|---|
| 400 | `invalid_input` | 请求体/参数校验失败 |
| 401 | `unauthorized` | 未认证 / token 无效或过期 |
| 403 | `forbidden` | 角色无权或非本人数据 |
| 404 | `not_found` | 资源不存在 |
| 409 | `invalid_state_transition` | 非法状态转移 |
| 409 | `conflict` | 唯一约束冲突 / 幂等键冲突 |
| 422 | `validation_error` | Pydantic 请求体校验失败 |
| 500 | `internal_error` | 未预期错误 |

---

## 1. 注册

- **HTTP**：`POST`
- **URL**：`/auth/register`
- **需要登录**：否
- **允许角色**：公开（仅注册 `customer`）

**请求参数**：无

**请求体**：

```json
{
  "email": "customer@example.com",
  "password": "secret123",
  "name": "张客户"
}
```

**返回结构** `201`：

```json
{
  "id": "<uuid>",
  "email": "customer@example.com",
  "name": "张客户",
  "role": "customer"
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | 密码 < 8 位 / 邮箱格式错误 / 缺字段 |
| 409 | `conflict` | 邮箱已注册 |
| 422 | `validation_error` | 请求体 schema 不符 |

**幂等要求**：无（重复注册返回 409，需重试则先查邮箱）。

**后台任务**：无。

---

## 2. 登录

- **HTTP**：`POST`
- **URL**：`/auth/login`
- **需要登录**：否
- **允许角色**：公开

**请求参数**：无

**请求体**：

```json
{ "email": "customer@example.com", "password": "secret123" }
```

**返回结构** `200`：

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": { "id": "<uuid>", "email": "...", "name": "...", "role": "customer" }
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 401 | `unauthorized` | 邮箱不存在 / 密码错误（不区分） |
| 403 | `forbidden` | `is_active=false`（账号停用） |
| 422 | `validation_error` | 请求体 schema 不符 |

**幂等要求**：无。

**后台任务**：无。

---

## 3. 当前用户

- **HTTP**：`GET`
- **URL**：`/auth/me`
- **需要登录**：是
- **允许角色**：`all`

**请求参数**：无（token 中解析）

**请求体**：无

**返回结构** `200`：

```json
{
  "id": "<uuid>",
  "email": "...",
  "name": "...",
  "role": "agent",
  "is_active": true,
  "created_at": "2026-08-09T08:00:00Z"
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 401 | `unauthorized` | 未认证 / token 失效 |
| 404 | `not_found` | token 指向的用户不存在 |

**幂等要求**：无。

**后台任务**：无。

---

## 4. 创建工单

- **HTTP**：`POST`
- **URL**：`/tickets`
- **需要登录**：是
- **允许角色**：`customer`（staff 走 `/admin/tickets`）

**请求参数**（header）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `Idempotency-Key` | 否（推荐） | UUID；重复请求返回首次结果 |

**请求体**：

```json
{
  "title": "无法登录账号",
  "description": "登录时提示密码错误，但密码是正确的。"
}
```

**返回结构** `201`：

```json
{
  "id": "<uuid>",
  "title": "无法登录账号",
  "status": "open",
  "priority": "normal",
  "classification": "",
  "summary": "",
  "sentiment": "neutral",
  "created_at": "2026-08-09T08:00:00Z"
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | title/description 为空或超长（title ≤200，description ≤10000） |
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | 非 customer 调用此接口 |
| 409 | `conflict` | 幂等键存在但 request_hash 不一致 |
| 422 | `validation_error` | 请求体 schema 不符 |

**幂等要求**：是——`Idempotency-Key` 重复请求返回首次响应快照（`200`），不重复建单；不传 key 则不幂等。

**后台任务**：是——创建成功即入队 `ticket_analysis:{ticket_id}`（AI 分类/摘要/优先级/情绪）。

---

## 5. 工单列表

- **HTTP**：`GET`
- **URL**：`/tickets`
- **需要登录**：是
- **允许角色**：`all`（customer 强制只看本人）

**请求参数**（query）：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` | int | 1 | 页码 ≥1 |
| `page_size` | int | 20 | 1–100 |
| `status` | str | — | 逗号分隔多选 |
| `priority` | str | — | 逗号分隔多选 |
| `classification` | str | — | 精确筛选 |
| `sentiment` | str | — | 精确筛选 |
| `search` | str | — | 标题/描述关键字 |
| `assignee_id` | uuid | — | 仅 staff |
| `mine` | bool | false | 仅 staff，只看处理人是自己 |
| `sort` | str | `updated_at` | `created_at` / `updated_at` / `priority`，前缀 `-` 倒序 |

**请求体**：无

**返回结构** `200`：

```json
{
  "items": [
    {
      "id": "<uuid>",
      "title": "无法登录账号",
      "status": "open",
      "priority": "high",
      "classification": "account",
      "summary": "客户反馈登录密码错误。",
      "sentiment": "negative",
      "customer_name": "张客户",
      "assignee_name": null,
      "reply_count": 2,
      "created_at": "2026-08-09T08:00:00Z",
      "updated_at": "2026-08-09T08:05:00Z"
    }
  ],
  "total": 42,
  "page": 1,
  "page_size": 20,
  "pages": 3
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | 分页参数非法 |
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | 无权限的筛选参数 |

**幂等要求**：无（GET 天然幂等）。

**后台任务**：无。

---

## 6. 工单详情

- **HTTP**：`GET`
- **URL**：`/tickets/{id}`
- **需要登录**：是
- **允许角色**：`all`（customer 仅本人）

**请求参数**（path）：`id`（UUID）

**请求体**：无

**返回结构** `200`：

```json
{
  "id": "<uuid>",
  "title": "...",
  "description": "...",
  "status": "open",
  "priority": "high",
  "classification": "account",
  "summary": "...",
  "sentiment": "negative",
  "customer": { "id": "<uuid>", "name": "张客户", "email": "..." },
  "assignee": { "id": "<uuid>", "name": "客服一号" },
  "replies": [
    {
      "id": "<uuid>",
      "content": "...",
      "is_ai_suggestion": true,
      "is_sent": false,
      "sender_name": "客服一号",
      "created_at": "..."
    }
  ],
  "evaluation": { "rating": 5, "comment": "很满意" },
  "ai_job": {
    "job_type": "ticket_analysis",
    "status": "succeeded",
    "result": { "category": "account", "summary": "...", "priority": "high", "sentiment": "negative", "confidence": 0.91, "reason": "..." },
    "error_message": null
  },
  "created_at": "...",
  "updated_at": "..."
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | customer 访问他人工单 |
| 404 | `not_found` | 工单不存在 |

**幂等要求**：无。

**后台任务**：无。

---

## 7. 状态变更

- **HTTP**：`POST`
- **URL**：`/tickets/{id}/transition`
- **需要登录**：是
- **允许角色**：`all`（具体见事件角色表，state-machine.md）

**请求参数**（path）：`id`（UUID）

**请求体**：

```json
{ "event": "start_review" }
```

**返回结构** `200`：

```json
{
  "id": "<uuid>",
  "status": "in_review",
  "allowed_events": ["send_reply", "cancel"],
  "updated_at": "2026-08-09T09:00:00Z"
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | 事件名非法 |
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | 角色无权触发该事件（如 customer 触发 `start_review`） |
| 404 | `not_found` | 工单不存在 |
| 409 | `invalid_state_transition` | 当前状态不允许该事件（含终态） |

**幂等要求**：无（重复触发非法转移 → 409；已转移则状态不同，自然拒收）。

**后台任务**：无（同步写库 + 审计）。

---

## 8. AI 分析结果查询

- **HTTP**：`GET`
- **URL**：`/tickets/{id}/analysis`
- **需要登录**：是
- **允许角色**：`all`（customer 仅本人）

**请求参数**（path）：`id`（UUID）

**请求体**：无

**返回结构** `200`：

```json
{
  "job_type": "ticket_analysis",
  "status": "succeeded",
  "result": {
    "category": "account",
    "summary": "客户反馈登录密码错误。",
    "priority": "high",
    "sentiment": "negative",
    "confidence": 0.91,
    "reason": "涉及账号登录核心功能，情绪负面"
  },
  "attempts": 1,
  "error_message": null,
  "created_at": "...",
  "updated_at": "..."
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | 非本人 customer |
| 404 | `not_found` | 工单或分析任务不存在 |

**幂等要求**：无。

**后台任务**：无（只读）。

---

## 9. AI 回复建议

- **HTTP**：`POST`
- **URL**：`/tickets/{id}/reply-suggestion`
- **需要登录**：是
- **允许角色**：`staff`（agent / admin）

**请求参数**（path）：`id`（UUID）

**请求体**：

```json
{ "top_k": 5 }
```

**返回结构** `202`：

```json
{
  "job_id": "<uuid>",
  "job_type": "reply_suggestion",
  "status": "pending"
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | top_k 不在 1–10 |
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | customer 调用 |
| 404 | `not_found` | 工单不存在 |
| 409 | `invalid_state_transition` | 工单处于 `closed` / `canceled` |

**幂等要求**：是——稳定 job_id `reply_suggestion:{ticket_id}` 已在队列/运行则返回已有 job，不重复入队。

**后台任务**：是——生成 `is_ai_suggestion=true, is_sent=false` 的建议草稿。

---

## 10. 审核回复

- **HTTP**：`PATCH`
- **URL**：`/tickets/{id}/replies/{reply_id}` （对建议草稿的编辑/确认）
- **需要登录**：是
- **允许角色**：`staff`

**请求参数**（path）：`id`、`reply_id`（UUID）

**请求体**：

```json
{ "content": "经核实的最终回复内容..." }
```

**返回结构** `200`：

```json
{
  "id": "<uuid>",
  "content": "经核实的最终回复内容...",
  "is_ai_suggestion": false,
  "is_sent": false
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | content 为空 |
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | customer / 非本工单 staff |
| 404 | `not_found` | 回复不存在或不属于该工单 |
| 409 | `conflict` | 回复已发送，不可再编辑 |

**幂等要求**：无。

**后台任务**：无。写审计 `reply.reviewed`。

---

## 11. 发送回复

- **HTTP**：`POST`
- **URL**：`/tickets/{id}/replies`
- **需要登录**：是
- **允许角色**：`staff`

**请求参数**（path）：`id`（UUID）

**请求体**：

```json
{
  "content": "经核实的最终回复内容...",
  "source_reply_id": "<uuid>"
}
```

**返回结构** `201`：

```json
{ "id": "<uuid>", "is_sent": true, "created_at": "..." }
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | content 为空 |
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | customer |
| 404 | `not_found` | 工单不存在 |
| 409 | `invalid_state_transition` | 工单非 `in_review`（须先 `start_review`） |
| 409 | `conflict` | `source_reply_id` 不属于该工单 |

**幂等要求**：无（发送成功即 `replied`，重复发送因状态不符被 409 拦截）。

**后台任务**：无（同步）。发送成功自动触发 `open→in_review` 已完成、`in_review→replied` 转移，写审计 `reply.sent`。

---

## 12. 关闭工单

- **HTTP**：`POST`
- **URL**：`/tickets/{id}/transition`（`event: "close"`）
- **需要登录**：是
- **允许角色**：`staff`

与第 7 条共用接口，事件 `close`：

```json
{ "event": "close" }
```

返回结构同第 7 条（`status: "closed"`）。错误码同第 7 条 + 追加条件：`replied → closed` 合法，`open → closed` 返回 `409`。

**幂等要求**：无（终态重复触发 → 409）。

**后台任务**：无。写审计 `ticket.status_changed`。

---

## 13. 创建评价

- **HTTP**：`POST`
- **URL**：`/tickets/{id}/evaluation`
- **需要登录**：是
- **允许角色**：`customer`（仅工单提交人本人）

**请求参数**（path）：`id`（UUID）

**请求体**：

```json
{ "rating": 5, "comment": "很满意" }
```

**返回结构** `201`：

```json
{ "id": "<uuid>", "rating": 5, "comment": "很满意", "created_at": "..." }
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | rating 不在 1–5 |
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | 非工单提交人 / 非 customer |
| 404 | `not_found` | 工单不存在 |
| 409 | `conflict` | 已评价过（一单一评） |
| 409 | `invalid_state_transition` | 工单非 `closed` |

**幂等要求**：无（唯一约束兜底，重复 → 409）。

**后台任务**：无。写审计 `evaluation.created`。

---

## 14. 知识库上传

- **HTTP**：`POST`
- **URL**：`/knowledge`
- **需要登录**：是
- **允许角色**：`staff`

**请求参数**：`multipart/form-data`，字段 `file`。

**请求体**：文件流

**返回结构** `201`：

```json
{
  "id": "<uuid>",
  "title": "产品手册",
  "source_type": "pdf",
  "file_name": "产品手册.pdf",
  "file_size_bytes": 204800,
  "status": "processing"
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | 非 pdf/txt / 超 20MB / 空文件 |
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | customer |

**幂等要求**：无（重复上传产生新文档；如需去重由前端确认文件名）。

**后台任务**：是——入队 `knowledge_index:{item_id}`（切片 + Embedding + 写向量）。

---

## 15. 知识库搜索

- **HTTP**：`POST`
- **URL**：`/knowledge/search`
- **需要登录**：是
- **允许角色**：`staff`

**请求参数**：无

**请求体**：

```json
{ "query": "如何重置密码", "top_k": 5 }
```

**返回结构** `200`：

```json
{
  "items": [
    {
      "chunk_id": "<uuid>",
      "content": "重置密码的步骤...",
      "similarity": 0.87,
      "knowledge_item_id": "<uuid>",
      "title": "账号安全手册"
    }
  ]
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 400 | `invalid_input` | query 为空 / top_k 越界 |
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | customer |
| 500 | `internal_error` | 检索失败 |

**幂等要求**：无。

**后台任务**：无（同步检索）。

---

## 16. 审计日志查询

- **HTTP**：`GET`
- **URL**：`/admin/audit-logs`
- **需要登录**：是
- **允许角色**：`admin`

**请求参数**（query）：

| 参数 | 类型 | 说明 |
|---|---|---|
| `page` / `page_size` | int | 分页 |
| `actor_id` | uuid | 操作者 |
| `action` | str | 动作 |
| `entity_type` | str | 实体类型 |
| `entity_id` | uuid | 实体 |
| `created_from` / `created_to` | datetime | 时间范围 |

**请求体**：无

**返回结构** `200`：

```json
{
  "items": [
    {
      "id": "<uuid>",
      "actor_name": "客服一号",
      "action": "reply.sent",
      "entity_type": "ticket",
      "entity_id": "<uuid>",
      "old_value": null,
      "new_value": { "status": "replied" },
      "ip_address": "127.0.0.1",
      "created_at": "2026-08-09T09:10:00Z"
    }
  ],
  "total": 128,
  "page": 1,
  "page_size": 20,
  "pages": 7
}
```

**错误码**：

| HTTP | code | 条件 |
|---|---|---|
| 401 | `unauthorized` | 未认证 |
| 403 | `forbidden` | 非 admin |
| 400 | `invalid_input` | 筛选参数非法 |

**幂等要求**：无。

**后台任务**：无。

---

## 17. 补充接口（辅助，供前端对齐）

| 接口 | 说明 | 角色 |
|---|---|---|
| `GET /health` | 健康检查 `{ status: "ok" }` | 公开 |
| `GET /admin/users` | 用户列表（分页 + 角色筛选） | admin |
| `POST /admin/users` | 创建 agent/admin 用户 | admin |
| `PATCH /admin/users/{id}` | 改角色 / 停用 | admin |
| `GET /stats/tickets` | 工单统计（by_status / by_priority） | staff |
| `GET /knowledge` | 知识库文档列表（分页 + 状态筛选） | staff |
| `GET /knowledge/{id}` | 文档详情（含 status / error_message） | staff |
| `DELETE /knowledge/{id}` | 删除文档（级联向量）→ 204 | staff |
| `POST /admin/tickets` | staff 代建工单（`customer_email` 字段） | staff |
| `PATCH /tickets/{id}` | 更新工单（title/description/assignee） | all（受限） |

---

## 18. 前端 API 对齐（Vue）

| 前端模块 | 接口 |
|---|---|
| 登录/注册/当前用户 | POST /auth/login、POST /auth/register、GET /auth/me |
| 我的工单 | GET /tickets、POST /tickets、GET /tickets/{id}、GET /tickets/{id}/analysis |
| 客服工作台 | GET /tickets（staff）、GET /tickets/{id}、POST /tickets/{id}/transition、POST /tickets/{id}/reply-suggestion、PATCH /tickets/{id}/replies/{reply_id}、POST /tickets/{id}/replies |
| 工单详情（评价） | GET /tickets/{id}、POST /tickets/{id}/evaluation |
| 知识库 | POST /knowledge、GET /knowledge、GET /knowledge/{id}、DELETE /knowledge/{id}、POST /knowledge/search |
| 用户管理 | GET /admin/users、POST /admin/users、PATCH /admin/users/{id} |
| 审计 | GET /admin/audit-logs |
| 统计 | GET /stats/tickets |

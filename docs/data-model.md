# 数据模型设计

版本：v1.0 · 状态：Draft · 最后更新：2026-08-09

## 约定

- 主键：`UUID`（`uuid4` 生成），统一 `id` 字段。
- 时间：`timestamptz`，默认 `now()`，统一 `created_at` / `updated_at`。
- 枚举：使用 `VARCHAR + CHECK 约束`（与项目一 `documents.status` 一致），不用原生 enum 类型，便于迁移。
- 软删除：**不使用**。删除采用硬删除 + 审计日志记录，避免复杂的历史隔离。
- 时区：一律 UTC 存储，展示层本地化。
- 删除策略：父表删除子表采用 `ON DELETE CASCADE`，业务删除记录审计。

## 关系总览

```
users 1 ─── * tickets 1 ─── * ticket_replies
users 1 ─── * evaluations
users 1 ─── * ai_processing_jobs
tickets 1 ─── 0..1 ai_processing_jobs（latest）
tickets 1 ─── 1 evaluations (0..1)
admin 1 ─── * audit_logs (操作者)
tickets 1 ─── * audit_logs (实体)
knowledge_items 1 ─── * knowledge_chunks
```

---

## 表 1：users

用户表（customer / agent / admin）。

| 字段 | 类型 | 空 | 默认 | 索引 | 唯一 | 外键 | 说明 |
|---|---|---|---|---|---|---|---|
| id | UUID | 否 | uuid4 | PK | — | — | 主键 |
| email | VARCHAR(255) | 否 | — | — | **唯一** | — | 登录账号，小写存储 |
| password_hash | VARCHAR(255) | 否 | — | — | — | — | bcrypt 哈希，**不存明文** |
| name | VARCHAR(100) | 否 | — | — | — | — | 显示名 |
| role | VARCHAR(20) | 否 | `customer` | idx | — | — | customer / agent / admin，CHECK |
| is_active | BOOLEAN | 否 | `true` | — | — | — | 停用后不可登录 |
| created_at | timestamptz | 否 | now() | — | — | — | 注册时间 |
| updated_at | timestamptz | 否 | now() | — | — | — | 更新时间（onupdate） |

**约束**

- `CHECK (role IN ('customer', 'agent', 'admin'))` → `ck_users_role`
- `CHECK (length(email) > 0 AND length(name) > 0)` → `ck_users_email_name_not_blank`
- `UNIQUE (email)` → `uq_users_email`

**索引**

- `idx_users_role`

**删除策略**：admin 删除用户 → 软停用（`is_active = false`）**不硬删**，以保留工单、评价、审计的可追溯性。工单/评价/审计 FK 为 `RESTRICT`。

---

## 表 2：tickets

工单主表。

| 字段 | 类型 | 空 | 默认 | 索引 | 唯一 | 外键 | 说明 |
|---|---|---|---|---|---|---|---|
| id | UUID | 否 | uuid4 | PK | — | — | 主键 |
| customer_id | UUID | 否 | — | idx | — | FK→users.id ON DELETE RESTRICT | 提交人 |
| title | VARCHAR(200) | 否 | — | — | — | — | 标题 |
| description | TEXT | 否 | — | — | — | — | 问题描述（纯文本） |
| status | VARCHAR(20) | 否 | `open` | idx | — | — | open / in_review / replied / closed / canceled，CHECK |
| priority | VARCHAR(20) | 否 | `normal` | idx | — | — | AI 生成，low / normal / high / urgent，CHECK |
| classification | VARCHAR(50) | 否 | — | idx | — | — | AI 分类标签（自由文本，规范化后存） |
| summary | TEXT | 否 | — | — | — | — | AI 摘要（无则空串） |
| sentiment | VARCHAR(20) | 否 | — | idx | — | — | AI 情绪：positive / neutral / negative，CHECK |
| assignee_id | UUID | 是 | NULL | idx | — | FK→users.id ON DELETE SET NULL | 处理人（agent/admin） |
| created_at | timestamptz | 否 | now() | idx | — | — | 创建时间 |
| updated_at | timestamptz | 否 | now() | — | — | — | 最后更新时间 |

**约束**

- `CHECK (status IN ('open', 'in_review', 'replied', 'closed', 'canceled'))` → `ck_tickets_status`
- `CHECK (priority IN ('low', 'normal', 'high', 'urgent'))` → `ck_tickets_priority`
- `CHECK (sentiment IN ('positive', 'neutral', 'negative'))` → `ck_tickets_sentiment`
- `CHECK (length(title) > 0 AND length(description) > 0)` → `ck_tickets_title_description_not_blank`

**索引**

- `idx_tickets_status`
- `idx_tickets_priority`
- `idx_tickets_classification`
- `idx_tickets_sentiment`
- `idx_tickets_customer_id`
- `idx_tickets_assignee_id`
- `idx_tickets_created_at`

> 设计决策：`classification` 用自由文本 VARCHAR(50) 存规范化后的标签（如 `billing`、`account`、`technical`），便于扩展分类体系；MVP 不做分类字典表。`priority` / `sentiment` 用固定枚举。

**删除策略**：工单不可被普通接口硬删。admin 需走 `DELETE /tickets/{id}`（RESTRICT 语义：有回复/评价时拒绝并提示），删除动作写审计日志。`ticket_replies` / `evaluations` / `ai_processing_jobs` 级联删除。

---

## 表 3：ticket_replies

工单回复（人工发送的回复 + AI 建议草稿）。

| 字段 | 类型 | 空 | 默认 | 索引 | 唯一 | 外键 | 说明 |
|---|---|---|---|---|---|---|---|
| id | UUID | 否 | uuid4 | PK | — | — | 主键 |
| ticket_id | UUID | 否 | — | idx | — | FK→tickets.id ON DELETE CASCADE | 所属工单 |
| sender_id | UUID | 否 | — | — | — | FK→users.id ON DELETE RESTRICT | 回复人 |
| content | TEXT | 否 | — | — | — | — | 回复正文 |
| is_ai_suggestion | BOOLEAN | 否 | `false` | — | — | — | 是否由 AI 生成的建议草稿 |
| is_sent | BOOLEAN | 否 | `false` | — | — | — | 是否已发送（审核通过） |
| created_at | timestamptz | 否 | now() | — | — | — | 创建时间 |

**约束**

- `CHECK (length(content) > 0)` → `ck_ticket_replies_content_not_blank`

**索引**

- `idx_ticket_replies_ticket_id`

**删除策略**：级联随工单删除。单个回复不提供删除接口。

---

## 表 4：knowledge_items

知识库文档（RAG 来源）。

| 字段 | 类型 | 空 | 默认 | 索引 | 唯一 | 外键 | 说明 |
|---|---|---|---|---|---|---|---|
| id | UUID | 否 | uuid4 | PK | — | — | 主键 |
| title | VARCHAR(255) | 否 | — | — | — | — | 文档标题 |
| content | TEXT | 否 | — | — | — | — | 完整文本（TXT）或解析后的纯文本（PDF） |
| source_type | VARCHAR(20) | 否 | — | — | — | — | txt / pdf，CHECK |
| file_name | VARCHAR(255) | 否 | — | — | — | — | 原始文件名 |
| file_size_bytes | INTEGER | 否 | — | — | — | — | 文件大小，CHECK > 0 |
| status | VARCHAR(20) | 否 | `processing` | idx | — | — | processing / ready / failed，CHECK |
| error_message | TEXT | 是 | NULL | — | — | — | 处理失败原因 |
| uploaded_by | UUID | 否 | — | — | — | FK→users.id ON DELETE RESTRICT | 上传人 |
| created_at | timestamptz | 否 | now() | idx | — | — | 上传时间 |

**约束**

- `CHECK (status IN ('processing', 'ready', 'failed'))` → `ck_knowledge_items_status`
- `CHECK (source_type IN ('txt', 'pdf'))` → `ck_knowledge_items_source_type`
- `CHECK (file_size_bytes > 0)` → `ck_knowledge_items_file_size_positive`

**索引**

- `idx_knowledge_items_status`
- `idx_knowledge_items_created_at`

**删除策略**：删除知识库文档 → 级联删除 `knowledge_chunks`（含向量）。删除动作写审计日志。

---

## 表 5：knowledge_chunks

知识库文本切片，含 pgvector 向量。

| 字段 | 类型 | 空 | 默认 | 索引 | 唯一 | 外键 | 说明 |
|---|---|---|---|---|---|---|---|
| id | UUID | 否 | uuid4 | PK | — | — | 主键 |
| knowledge_item_id | UUID | 否 | — | — | 复合唯一 | FK→knowledge_items.id ON DELETE CASCADE | 所属文档 |
| chunk_index | INTEGER | 否 | — | — | 复合唯一 | — | 切片序号 |
| content | TEXT | 否 | — | — | — | — | 切片文本 |
| embedding | VECTOR(1536) | 否 | — | HNSW/IVFFlat | — | — | 向量（与项目一保持 1536 维） |
| created_at | timestamptz | 否 | now() | — | — | — | 创建时间 |

**约束**

- `UNIQUE (knowledge_item_id, chunk_index)` → `uq_knowledge_chunks_item_chunk_index`
- `CHECK (chunk_index >= 0)` → `ck_knowledge_chunks_index_non_negative`
- `CHECK (length(trim(content)) > 0)` → `ck_knowledge_chunks_content_not_blank`

**索引**

- `idx_knowledge_chunks_item_id`
- **向量索引**：`CREATE INDEX ix_knowledge_chunks_embedding_hnsw ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)`（推荐 HNSW；数据量小也可用 IVFFlat）

> 设计决策：切片表与知识库主表分离（复用项目一 documents / document_chunks 模式），便于检索时只扫描向量表。

---

## 表 6：audit_logs

操作审计日志（append-only，不做 UPDATE/DELETE）。

| 字段 | 类型 | 空 | 默认 | 索引 | 唯一 | 外键 | 说明 |
|---|---|---|---|---|---|---|---|
| id | UUID | 否 | uuid4 | PK | — | — | 主键 |
| actor_id | UUID | 是 | NULL | idx | — | FK→users.id ON DELETE SET NULL | 操作者（系统任务为 NULL） |
| action | VARCHAR(50) | 否 | — | idx | — | — | 动作：ticket.created / ticket.status_changed / reply.sent / reply.reviewed / knowledge.deleted / user.created ... |
| entity_type | VARCHAR(30) | 否 | — | idx | — | — | ticket / reply / knowledge_item / user / evaluation |
| entity_id | UUID | 是 | NULL | idx | — | — | 实体 ID（无则 NULL） |
| old_value | JSONB | 是 | NULL | — | — | — | 变更前快照 |
| new_value | JSONB | 是 | NULL | — | — | — | 变更后快照 |
| ip_address | VARCHAR(45) | 是 | NULL | — | — | — | 来源 IP（IPv6 长度） |
| created_at | timestamptz | 否 | now() | idx | — | — | 记录时间 |

**索引**

- `idx_audit_logs_actor_id`
- `idx_audit_logs_action`
- `idx_audit_logs_entity_type`
- `idx_audit_logs_entity_id`
- `idx_audit_logs_created_at`

**约束**

- `CHECK (length(action) > 0 AND length(entity_type) > 0)` → `ck_audit_logs_not_blank`

**删除策略**：不提供删除接口；仅在数据库维护时归档。硬删除任何用户的 API 不存在。

---

## 表 7：evaluations

客户对已关闭工单的评价。

| 字段 | 类型 | 空 | 默认 | 索引 | 唯一 | 外键 | 说明 |
|---|---|---|---|---|---|---|---|
| id | UUID | 否 | uuid4 | PK | — | — | 主键 |
| ticket_id | UUID | 否 | — | — | **唯一** | FK→tickets.id ON DELETE CASCADE | 被评价工单（一单一评，仅 `closed` 可评价） |
| customer_id | UUID | 否 | — | idx | — | FK→users.id ON DELETE RESTRICT | 评价人（必须=工单提交人） |
| rating | SMALLINT | 否 | — | — | — | — | 1–5，CHECK |
| comment | TEXT | 是 | NULL | — | — | — | 评论（可选） |
| created_at | timestamptz | 否 | now() | — | — | — | 评价时间 |

**约束**

- `UNIQUE (ticket_id)` → `uq_evaluations_ticket_id`
- `CHECK (rating BETWEEN 1 AND 5)` → `ck_evaluations_rating_range`
- 业务校验（服务层）：`tickets.status` 必须为 `closed` 且 `evaluations.customer_id = tickets.customer_id`。

**索引**

- `idx_evaluations_customer_id`

**删除策略**：级联随工单删除。不提供单独删除接口。

---

## 表 8：ai_processing_jobs

AI 处理任务记录（分类/摘要/优先级/情绪 + 回复建议）。任务幂等 + 失败重试的持久化依据。

| 字段 | 类型 | 空 | 默认 | 索引 | 唯一 | 外键 | 说明 |
|---|---|---|---|---|---|---|---|
| id | UUID | 否 | uuid4 | PK | — | — | 主键 |
| ticket_id | UUID | 否 | — | idx | — | FK→tickets.id ON DELETE CASCADE | 关联工单 |
| job_type | VARCHAR(30) | 否 | — | — | 复合唯一 | — | `ticket_analysis` / `reply_suggestion` |
| business_key | VARCHAR(255) | 否 | — | — | — | — | RQ 稳定 job id，如 `ticket_analysis:{ticket_id}` |
| status | VARCHAR(20) | 否 | `pending` | idx | — | — | pending / processing / succeeded / failed，CHECK |
| payload | JSONB | 是 | NULL | — | — | — | 任务入参快照 |
| result | JSONB | 是 | NULL | — | — | — | 成功结果（结构化输出 Pydantic 校验后） |
| error_message | TEXT | 是 | NULL | — | — | — | 失败原因 |
| attempts | INTEGER | 否 | `0` | — | — | — | 已尝试次数 |
| max_attempts | INTEGER | 否 | `3` | — | — | — | 最大重试次数（RQ 重试配置） |
| last_error_at | timestamptz | 是 | NULL | — | — | — | 最近失败时间 |
| created_at | timestamptz | 否 | now() | — | — | — | 创建时间 |
| updated_at | timestamptz | 否 | now() | — | — | — | 更新时间 |

**约束**

- `CHECK (job_type IN ('ticket_analysis', 'reply_suggestion'))` → `ck_ai_jobs_type`
- `CHECK (status IN ('pending', 'processing', 'succeeded', 'failed'))` → `ck_ai_jobs_status`
- `UNIQUE (ticket_id, job_type)` → `uq_ai_jobs_ticket_job_type`

**索引**

- `idx_ai_jobs_status`

> 设计决策：`UNIQUE (ticket_id, job_type)` 是 RQ 幂等的基础——同一工单同一类型的 AI 任务只有一条记录，重复入队直接复用/跳过，从数据库层面杜绝重复处理。

**删除策略**：级联随工单删除。保留失败记录用于排查和展示重试。

---

## 表 9：idempotency_keys

工单创建幂等映射表（append-only，保存首次响应快照）。

| 字段 | 类型 | 空 | 默认 | 索引 | 唯一 | 外键 | 说明 |
|---|---|---|---|---|---|---|---|
| id | UUID | 否 | uuid4 | PK | — | — | 主键 |
| key | VARCHAR(128) | 否 | — | — | **唯一** | — | 请求头 `Idempotency-Key`（UUID 规范化） |
| endpoint | VARCHAR(64) | 否 | — | — | — | — | 所属端点（`POST /tickets`） |
| actor_id | UUID | 否 | — | idx | — | FK→users.id ON DELETE RESTRICT | 请求者 |
| request_hash | VARCHAR(64) | 否 | — | — | — | — | 请求体 SHA-256，防止同 key 不同 body |
| response_json | JSONB | 否 | — | — | — | — | 首次成功响应快照 |
| created_at | timestamptz | 否 | now() | — | — | — | 首次请求时间 |

**约束**

- `UNIQUE (key)` → `uq_idempotency_keys_key`
- `CHECK (length(key) > 0)` → `ck_idempotency_keys_key_not_blank`

**索引**

- `idx_idempotency_keys_actor_id`

**删除策略**：随到期策略清理（TTL / 定期归档），MVP 不做自动清理，仅记录。工单创建事务失败时幂等键一并回滚。

---

## 迁移顺序（Alembic）

| 迁移 | 内容 |
|---|---|
| 0001 | `CREATE EXTENSION vector`；users、tickets 基础表 |
| 0002 | ticket_replies、evaluations、idempotency_keys |
| 0003 | knowledge_items、knowledge_chunks（含 vector 列与 HNSW 索引） |
| 0004 | audit_logs、ai_processing_jobs |
| 0005 | 增量：补充复合索引 / 调整字段（如需要） |

> 与项目一一致：Alembic `env.py` 从 `backend.app.models` 导入全部模型保证 autogenerate 完整。

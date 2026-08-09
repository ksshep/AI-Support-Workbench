# 工单状态机

版本：v1.0 · 状态：Draft · 最后更新：2026-08-09

## 1. 状态总览

```text
                ┌──────────────────────────────┐
                │                              ▼
  open ──start_review──▶ in_review ──send_reply──▶ replied ──close──▶ closed
    │                     │
    └──cancel──▶ canceled ┘
```

| 状态 | 含义 | 谁可见 |
|---|---|---|
| `open` | 客户已提交，等待客服认领/开始处理。AI 分析可能仍在进行或已就绪 | 全部（customer 仅本人） |
| `in_review` | 客服已接手，正在审阅 AI 建议、撰写回复 | agent / admin / 本人 customer |
| `replied` | 客服已发送回复，等待客户确认或自动到期关闭 | agent / admin / 本人 customer |
| `closed` | 处理完成，客户可评价 | 全部（customer 仅本人） |
| `canceled` | 工单被取消（客户撤销或客服确认无效），不可再流转、不可评价 | 全部（customer 仅本人） |

**终态**：`closed`、`canceled`。终态工单不可再发起任何状态转移。

---

## 2. 事件定义

| 事件 | 含义 | 触发人 | 角色限制 |
|---|---|---|---|
| `start_review` | 客服开始处理工单（认领） | agent / admin | 仅 agent / admin |
| `send_reply` | 客服发送人工审核后的回复 | agent / admin | 仅 agent / admin |
| `close` | 客服关闭工单（已回复） | agent / admin | 仅 agent / admin |
| `cancel` | 取消工单（撤销误提交 / 确认无效） | customer 或 agent/admin | open：customer、agent、admin；in_review：仅 agent / admin |

---

## 3. 状态转移表（显式）

```python
TRANSITIONS: dict[str, dict[str, str]] = {
    "open": {
        "start_review": "in_review",
        "cancel": "canceled",
    },
    "in_review": {
        "send_reply": "replied",
        "cancel": "canceled",
    },
    "replied": {
        "close": "closed",
    },
    "closed": {},   # 终态
    "canceled": {}, # 终态
}
```

### 3.1 事件 → 允许角色

```python
EVENT_ROLES: dict[str, tuple[str, ...]] = {
    "start_review": ("agent", "admin"),
    "send_reply": ("agent", "admin"),
    "close": ("agent", "admin"),
    "cancel": ("customer", "agent", "admin"),  # 角色见下方说明
}
```

`cancel` 的附加规则（服务层校验）：

- `open` 状态：customer（仅本人工单）、agent、admin 均可取消；
- `in_review` 状态：仅 agent / admin 可取消（客户不能取消客服已接手的工单）；
- `replied` / `closed` / `canceled`：不可取消。

---

## 4. 角色-状态-事件矩阵

| 当前状态 | 事件 | customer（本人） | agent | admin |
|---|---|---|---|---|
| `open` | `start_review` | ❌ | ✅ | ✅ |
| `open` | `cancel` | ✅ | ✅ | ✅ |
| `in_review` | `send_reply` | ❌ | ✅ | ✅ |
| `in_review` | `cancel` | ❌ | ✅ | ✅ |
| `replied` | `close` | ❌ | ✅ | ✅ |
| `closed` | —（终态） | ❌ | ❌ | ❌ |
| `canceled` | —（终态） | ❌ | ❌ | ❌ |

> `send_reply` 与 `close` 是**两个独立事件**：发送回复只置为 `replied`，关闭工单必须显式执行 `close`。这保证"已回复但客户尚未确认"与"处理完成"可区分，也是评价（仅 `closed`）的前提。

---

## 5. 非法转移示例

| 场景 | 当前状态 | 请求事件 | 结果 |
|---|---|---|---|
| 未处理先关闭 | `open` | `close` | `409 invalid_state_transition` |
| 重复关闭 | `closed` | `close` | `409` |
| 取消已关闭 | `closed` | `cancel` | `409` |
| 取消已回复 | `replied` | `cancel` | `409` |
| 终态再转移 | `canceled` | `start_review` | `409` |
| 客户取消 in_review | `in_review` | `cancel`（customer） | `403 forbidden` |
| 客户认领工单 | `open` | `start_review`（customer） | `403 forbidden` |
| 直接发回复 | `open` | `send_reply` | `409`（必须先 `start_review`） |

**响应示例（`409`）**：

```json
{
  "detail": {
    "code": "invalid_state_transition",
    "message": "Cannot move ticket from 'open' to 'closed'.",
    "current_status": "open",
    "allowed_events": ["start_review", "cancel"]
  }
}
```

---

## 6. 审计日志记录内容

每次成功状态转移写入一条 `audit_logs`：

| 字段 | 值 |
|---|---|
| `actor_id` | 操作者 user id（系统任务为 NULL） |
| `action` | `ticket.status_changed` |
| `entity_type` | `ticket` |
| `entity_id` | 工单 id |
| `old_value` | `{ "status": "open" }` |
| `new_value` | `{ "status": "in_review", "event": "start_review" }` |
| `ip_address` | 请求来源 IP（后台任务为 NULL） |

**其他操作的审计动作约定**：

| 操作 | `action` | `entity_type` |
|---|---|---|
| 创建工单 | `ticket.created` | ticket |
| 更新工单 | `ticket.updated` | ticket |
| 状态转移 | `ticket.status_changed` | ticket |
| 生成 AI 建议 | `reply.ai_suggestion_generated` | ticket |
| 审核（编辑建议后发送） | `reply.reviewed` | ticket |
| 发送回复 | `reply.sent` | ticket |
| 删除知识库文档 | `knowledge.deleted` | knowledge_item |
| 创建用户 | `user.created` | user |
| 修改用户 | `user.updated` | user |
| 删除工单 | `ticket.deleted` | ticket |
| 创建评价 | `evaluation.created` | evaluation |

> 约定：`reply.*` 的 `entity_type` 统一用 `ticket`（entity_id 为工单 id），避免回复无独立 id 时的空指针；`old_value` / `new_value` 记录关键字段快照（状态、内容、是否已审核）。

---

## 7. 并发安全

- 状态转移使用 `SELECT ... FOR UPDATE`（行锁）读取工单行，再校验转移表，避免并发重复关闭/重复发送。
- 转移与审计日志写入放在**同一事务**内提交。
- 数据库 `CHECK` 约束作为最终防线（`ck_tickets_status`），双保险。

---

## 8. 状态流转的派生触发

| 事件 | 副作用 |
|---|---|
| `send_reply` | `replied` 状态下，所有未发送的 `is_ai_suggestion=true` 草稿可保留（不自动清除） |
| `close` | 工单进入 `closed`，客户获得评价资格 |
| `cancel` | AI 任务若仍在排队，标记 `canceled`（见 `technical-design.md` 的 RQ 取消策略） |

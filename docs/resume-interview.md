# 简历与面试准备

版本：v1.0 · 状态：Draft · 最后更新：2026-08-09

## 1. 简历项目描述（可直接用）

### 项目名：AI Support Workbench — AI 客服工单协同平台

> 面向企业客服团队的 AI 工单与客服协同平台：客户提交工单后，系统通过 RQ 异步任务调用 AI 完成分类、摘要、优先级和情绪识别，并从企业知识库（pgvector 向量检索）生成回复建议；回复必须经客服人工审核后才能发送，客户可对处理结果评分，全程操作写入审计日志。
>
> **技术栈**：Python · FastAPI · PostgreSQL · pgvector · Redis · RQ · Vue 3 · TypeScript · Docker Compose · GitHub Actions
>
> **我的职责**：
> - 设计并实现 JWT 认证与三角色 RBAC（customer / agent / admin），数据范围隔离保证客户只能访问本人工单；
> - 用显式状态转移表实现工单状态机（open → in_review → replied → closed，含 canceled），非法转移返回业务错误并写入审计；
> - 抽象 ChatProvider / EmbeddingProvider 接口，Fake 与 OpenAI-compatible 双实现，结构化输出经 Pydantic schema 校验；AI 失败自动重试并降级，不影响人工流程；
> - 用 Redis + RQ 异步处理 AI 分析、Embedding 与回复建议，稳定 job_id + 数据库唯一约束实现任务幂等；工单创建支持 Idempotency-Key；
> - 实现知识库 PDF/TXT 上传、切片、pgvector 余弦检索与 RAG 回复建议闭环（建议 → 审核 → 发送 → 关闭 → 评价）；
> - 全量 pytest（110+ 用例，0 网络依赖），Docker Compose 一键启动，GitHub Actions 自动测试，按 Issue → 分支 → PR → Review 流程交付。

### 简历一句话版本

> 基于 FastAPI + RQ + pgvector 的 AI 客服工单协同平台，实现 AI 结构化分析、RAG 回复建议、人工审核闭环与全量审计，全链路测试与 Docker 一键部署。

---

## 2. 三条项目亮点

1. **AI 落地业务闭环且可控**：AI 只做分析和建议，回复必须人工审核——用"接口抽象 + 结构化输出校验 + 异步任务"三个机制同时保证 AI 能力可替换、输出可信、不失控，这是 AI 应用开发岗位最看重的工程化意识。
2. **异步任务与幂等设计**：AI 调用全部进 RQ worker，稳定业务 key + 数据库唯一约束双层幂等，失败自动重试且不重复处理；工单创建支持 Idempotency-Key。体现"生产环境可靠性"的思考。
3. **完整工程流程**：110+ 测试、显式状态机、全量审计日志、Issue→分支→PR→Review、CI 自动化——不是 demo，而是按团队协作标准交付的完整项目。

---

## 3. 十个面试问题与回答要点

### Q1. 为什么用 RQ 而不是 Celery？
- 项目定位单机单库，RQ 足够：worker/queue/job 模型简单，能讲清楚异步任务、重试、幂等；
- Celery 功能更强（定时、多队列、分布式），但引入调度、broker 结构的心智负担；
- 结论：先选简单可靠方案，等有定时任务或多队列需求再演进，是刻意的工程取舍。

### Q2. AI 结构化输出怎么保证格式稳定？
- 三层保证：① Prompt 要求纯 JSON + 给示例；② 真实 provider 用 JSON mode / function calling；③ 输出经 Pydantic `TicketAnalysis` schema 校验，解析失败抛 `StructuredOutputError`；
- 失败后自动重试 2 次，仍失败则记录错误、AI 字段降级为默认值，人工流程不受影响。

### Q3. 状态机为什么不用库，自己写转移表？
- 工单状态有限（5 态 5 事件），显式 `dict` 转移表足够表达且**白板可画**；
- 非法转移返回 409 + allowed_events，错误清晰；配合数据库 CHECK 约束双保险；
- 引入状态机库会让关键逻辑藏在库里，面试讲不清、排错更难。

### Q4. 怎么防止 AI 绕过人工审核？
- 接口层：系统**不存在**"AI 自动发送"接口；回复发送 `POST /tickets/{id}/replies` 只接收人工确认后的 content；
- 数据层：建议草稿 `is_ai_suggestion=true, is_sent=false`，只有 agent/admin 能改（PATCH 审核）再发送；
- 审计层：发送动作写 `reply.sent` 和 `reply.reviewed`，可追溯谁在何时确认。

### Q5. 任务幂等怎么做？
- 三层：① RQ 稳定 job_id（`ticket_analysis:{ticket_id}`），已排队/运行直接复用；② 数据库 `UNIQUE(ticket_id, job_type)` 兜底；③ 任务函数内先查状态，succeeded 直接返回；
- 工单创建幂等用 Idempotency-Key + `idempotency_keys` 表（同 key 同 body 返回首次快照，同 key 不同 body 409）。

### Q6. 数据权限怎么隔离？
- 两层：`require_roles` 校验角色；查询层强制数据范围——customer 的列表查询永远附加 `customer_id == current_user.id`，无法通过参数绕过；
- 越权返回 403，配合审计日志可追溯。

### Q7. 数据库为什么选 pgvector 而不是单独向量数据库？
- MVP 数据量（几千 chunk）pgvector 足够，还省去维护第二套数据库的运维成本；
- 向量检索与业务表同库，支持 SQL JOIN（检索只返回 ready 文档）和事务一致性；
- 1536 维 + HNSW 索引满足项目检索性能；后续数据量大了再考虑独立向量库，是演进方向。

### Q8. 后台任务失败怎么处理？
- RQ Retry（5s、30s 各一次）；每次失败写 `ai_processing_jobs.attempts` / `error_message` / `last_error_at`；
- 重试耗尽置 failed，前端显示"AI 暂不可用"，工单仍可人工处理——**AI 是增强不是依赖**。

### Q9. 审计日志怎么保证完整？
- 业务写 + 审计写在**同一事务**提交，业务失败则无日志，日志失败则业务回滚，杜绝"有操作无记录"；
- append-only，无删除/更新接口，admin 可查；
- 后台任务写审计 actor_id=NULL 区分系统动作。

### Q10. 项目一的 RAG 和项目二的 RAG 有什么区别？
- 复用设计思想：切片 → Embedding → pgvector 余弦检索 → 上下文构建；
- 项目二把知识库从"求职文档"泛化为"企业知识库"，并接入工单业务：检索结果要按工单描述过滤、建议必须人工审核、检索只查 ready 文档；
- 同时项目二新增了异步任务（RQ）、结构化输出、审计、幂等等工程能力，是把 RAG 真正嵌进业务闭环。

---

## 4. 项目当前边界（诚实表达）

- 单企业、单数据库、单体应用，无多租户；
- 无限流、无复杂权限模型（3 角色足够 MVP）；
- 后台任务用 RQ，无定时任务与多队列；
- 无 SSE / WebSocket，前端轮询任务状态；
- 无支付、短信、邮件、小程序；
- 未部署公网，仅 Docker Compose 本地/测试环境。

---

## 5. 后续演进方向

| 方向 | 说明 |
|---|---|
| SSE 流式输出 | 客服实时看到 AI 生成过程，提升体验 |
| Celery + 多队列 + Beat | 定时任务（如自动关闭超时工单）、并发控制 |
| 多租户 | `tenant_id` 全表加列 + 查询层隔离 |
| 限流与缓存 | Redis 对热工单、知识库检索结果缓存 |
| 分类字典表 | 替代自由文本 category，配合 AI few-shot |
| 知识库版本管理 | 文档更新后的向量增量同步 |
| 部署上线 | Docker Compose → 服务器 + HTTPS + 监控 |

---

## 6. 面试表达时间轴（30–60 秒讲完项目）

```
"我做了两个关联项目。第一个是求职资料 RAG 问答助手，验证了我能独立完成一个 AI 应用的全链路。
第二个是 AI 客服工单平台，核心是：工单进来后，RQ 异步让 AI 做分类、摘要、优先级、情绪识别，
再从 pgvector 知识库检索生成回复建议；建议必须人工审核才能发送，客户可以评分，全程审计。
工程上我重点做了三件事：接口抽象保证 AI 可替换、任务幂等保证可靠性、显式状态机保证业务正确。
整个项目 110+ 测试、Docker 一键启动、GitHub Actions 自动 CI。"
```

---

## 7. 投递前 checklist

- [ ] 仓库 README 完善（截图 + 启动 + 架构图）；
- [ ] 3 个代表性 PR 链接（状态机、幂等、RQ 任务）放进简历；
- [ ] Swagger 全接口过一遍；
- [ ] 能白板画出状态转移表；
- [ ] 两个项目的"一句话差异"能脱口而出；
- [ ] 准备好"项目边界"与"演进方向"各 3 条。

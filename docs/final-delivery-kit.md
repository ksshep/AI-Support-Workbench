# 最终交付材料

本文用于项目展示、简历填写、演示录制和面试复盘。项目均为本地 Docker Compose 部署，真实模型配置通过环境变量提供，仓库不包含密钥、个人资料或生产数据。

## 1. 简历项目描述

### 项目一：Career Knowledge Copilot

基于 FastAPI、PostgreSQL/pgvector 和 Vue 3 构建的通用文档 RAG 问答应用。实现 PDF 异步解析、页面文本持久化、文本切片、1536 维 Embedding、向量相似度检索、RAG 上下文构建和带引用的问答；通过 ChatProvider、EmbeddingProvider 及 OpenAI-compatible Provider 抽象支持不同模型平台切换，并使用 Docker Compose、Alembic 和 pytest 完成可复现交付。

### 项目二：AI Support Workbench

面向企业客服团队的 AI 工单协同平台。实现 JWT/RBAC、工单 CRUD 与显式状态机、Idempotency-Key、审计日志、Redis/RQ 异步 AI 分析、知识库 pgvector 检索、RAG 回复建议及人工审核发送闭环；前端使用 Vue 3/TypeScript，后端使用 FastAPI，配套 Docker Compose、GitHub Actions CI 和完整测试。

### 两个项目的一句话差异

项目一证明我能完成 RAG 应用全链路；项目二进一步证明我能把 AI 能力嵌入真实业务流程，并处理权限、状态、异步、幂等、审计和人工审核等工程问题。

## 2. 三分钟演示脚本

### 0:00–0:20 产品定位

说明这是一个 AI 客服工单协同平台，AI 负责分析和生成建议，客服保留最终发送权限，客户可以查看结果并评价。

### 0:20–0:45 客户端

用 customer 账号登录，创建一个关于退款或接口故障的工单。展示创建成功、工单状态为 `open`、列表筛选和详情页。强调创建接口支持 `Idempotency-Key`，重复提交不会产生重复工单。

### 0:45–1:20 异步 AI 分析

切换 agent 账号打开工单，触发 AI 分析。展示接口立即返回 `pending`，页面轮询任务状态，最终显示分类、摘要、优先级和情绪。说明 AI 调用在 RQ worker 中执行，失败不会破坏原工单。

### 1:20–2:00 知识库与 RAG 回复建议

进入知识库上传一份脱敏 TXT/PDF，等待状态变为 `ready`，执行关键词检索。回到工单触发回复建议，展示检索来源和 AI 草稿。强调 AI 只能创建 draft，不能直接发送。

### 2:00–2:35 人工审核闭环

审核草稿，分别展示通过和退回路径；通过后发送回复，工单状态变为 `replied`。关闭工单后切换 customer 账号，展示客户只能看到 `sent` 回复，并提交 1–5 分评价。

### 2:35–3:00 工程实现与收尾

打开 Swagger 或 README，说明 FastAPI、PostgreSQL/pgvector、Redis/RQ、Vue、Docker Compose 和 GitHub Actions。补充三个工程取舍：状态机显式转移表、RQ 稳定 job_id 加数据库唯一约束、AI 草稿必须人工审核。

## 3. 面试高频问题

1. **为什么用 RQ？** 当前规模使用 RQ 能清晰表达 worker、重试和幂等；需要定时任务、多队列或更复杂调度时再演进到 Celery。
2. **如何保证 AI 输出可用？** Provider 抽象隔离平台，结构化结果用 Pydantic 校验，失败记录任务状态并保留人工处理路径。
3. **为什么 AI 不能自动发送？** 发送接口只允许 reviewed 回复，AI 任务只能创建 draft，审核边界由服务层和状态约束共同保证。
4. **如何防重复任务？** RQ 使用稳定 job_id，数据库使用 `(ticket_id, job_type)` 唯一约束，任务执行前检查已有成功结果。
5. **如何防止客户越权？** JWT 识别身份，RBAC 校验角色，查询服务额外限制 `customer_id`，不能仅靠前端隐藏按钮。
6. **为什么 pgvector？** 业务数据和向量数据规模适合单库，SQL JOIN 和事务一致性更简单；规模扩大后可替换为独立向量数据库。
7. **后台任务失败怎么办？** 记录错误和重试次数，耗尽后标记 failed；工单仍可由客服人工处理。
8. **项目边界是什么？** 当前是单体、单租户、本地 Docker Compose，无公网部署、SSE、多租户和复杂限流。

## 4. 投递前检查

- [ ] 两个仓库 README 可从零启动，截图或演示视频链接可访问。
- [ ] `v1.0.0` Release 可访问，CI 主分支为绿色。
- [ ] 仓库中没有 `.env`、API Key、数据库密码、uploads 或真实简历。
- [ ] 能在 30 秒内说清两个项目的差异。
- [ ] 能画出工单状态机和 RAG 请求链路。
- [ ] 能解释一个可靠性设计（幂等/事务/重试）和一个 AI 安全设计（人工审核）。
- [ ] 演示使用脱敏数据和本地测试账号，不在录屏中展示密钥。

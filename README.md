# AI Support Workbench

面向企业客服团队的 AI 工单协同平台。客户提交工单后，客服可以查看工单、执行状态流转、审核 AI 回复建议并发送；客户可以查看处理结果并评价。AI 只生成草稿，不能绕过人工审核自动发送。

## 核心链路

```text
客户注册并提交工单
  -> 工单状态机与审计日志
  -> RQ 异步执行 AI 分析
  -> 知识库切片、Embedding、pgvector 检索
  -> 生成带来源的回复草稿
  -> 客服审核/退回/发送
  -> 关闭工单
  -> 客户评价
```

## 功能

- JWT 登录与三角色 RBAC：`customer`、`agent`、`admin`
- 工单 CRUD、分页筛选、显式状态机和 `Idempotency-Key`
- AI 分类、摘要、优先级、情绪分析，全部通过 RQ 异步执行
- PDF/TXT 知识库上传、切片、Embedding、pgvector 相似度检索
- RAG 回复建议，AI 只能创建 `draft`
- 人工审核后才允许发送，客户只能看到 `sent` 回复
- 回复、状态变化、评价等操作写入 append-only 审计日志
- Vue 3 + TypeScript 工作台，支持 customer、agent、admin 页面

## 技术栈

FastAPI、SQLAlchemy 2、Alembic、PostgreSQL + pgvector、Redis、RQ、Vue 3、TypeScript、Vite、Nginx、Docker Compose、pytest。

Chat 和 Embedding 均使用 Provider 接口及工厂。业务代码不依赖具体供应商；默认测试使用 fake provider，生产环境可配置任意 OpenAI-compatible 服务。

## 快速开始

准备 Docker Desktop，在项目根目录执行：

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

默认服务地址：

- 前端：http://localhost:5173
- Swagger：http://localhost:8001/docs
- 健康检查：http://localhost:8001/health
- PostgreSQL：`localhost:5433`
- Redis：`localhost:6379`

`.env` 只保存在本地，API Key、密码、上传文件和真实个人资料不得提交到 Git。

## Provider 配置

开发和测试可以使用：

```dotenv
LLM_PROVIDER=fake
EMBEDDING_PROVIDER=fake
```

接入 OpenAI-compatible 服务时，只需配置环境变量，不修改业务代码：

```dotenv
LLM_PROVIDER=compatible
LLM_API_KEY=your-chat-key
LLM_BASE_URL=https://example.com/v1
LLM_MODEL=your-chat-model

EMBEDDING_PROVIDER=compatible
EMBEDDING_API_KEY=your-embedding-key
EMBEDDING_BASE_URL=https://example.com/v1
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_DIMENSION=1536
```

Embedding 模型更换时，必须确认向量维度并执行对应 Alembic 迁移及历史数据重生成。非 OpenAI-compatible 平台需要新增 Adapter，不应把平台判断写进工单业务代码。

## 测试与开发

```bash
docker compose up -d db redis
python -m venv .venv
.venv\\Scripts\\activate       # Windows
pip install -r backend/requirements.txt
python -m pytest -q
git diff --check
npm --prefix frontend run build
```

测试使用独立数据库并强制 fake provider，不依赖真实网络或 API Key。完整设计、数据模型、API、状态机和开发流程见 [`docs/`](docs/)；产品和前端设计见 [`PRODUCT.md`](PRODUCT.md) 与 [`DESIGN.md`](DESIGN.md)。

## MVP 边界

当前不包含 SSE/WebSocket、多租户、微服务、Kubernetes、复杂统计大屏和 AI 自动发信。这些属于后续演进方向，当前优先保证权限、审计、异步任务、人工审核和数据一致性。

## 开发流程

项目采用 GitHub Flow：Issue -> feature 分支 -> 实现与测试 -> Swagger/手工验收 -> Pull Request -> Review -> 合并。详细约定见 [`docs/collaboration-workflow.md`](docs/collaboration-workflow.md)。

# AI Support Workbench

面向企业客服团队的 **AI 工单与客服协同平台**：客户提交工单后，AI 自动完成分类、摘要、优先级与情绪识别，并从企业知识库（pgvector）生成回复建议；回复**必须经客服人工审核**后方可发送，客户可评分，全程操作写入审计日志。

> 当前为 **MVP 设计阶段**：本仓库已包含完整产品与技术设计文档，尚未生成业务代码。开发按 `docs/development-plan.md` 的 3 周主线推进。

---

## 核心流程

```text
客户提交工单
  → 保存工单（支持 Idempotency-Key 幂等）
  → RQ 异步：AI 分类 / 摘要 / 优先级 / 情绪（Pydantic 校验）
  → 客服查看 AI 分析
  → 生成回复建议（RAG 检索知识库 → pgvector → 建议草稿）
  → 客服人工审核 / 编辑
  → 发送回复（in_review → replied）
  → 关闭工单（replied → closed）
  → 客户评价
  → 全程写入 audit_logs
```

## 状态机

```text
open ──start_review──▶ in_review ──send_reply──▶ replied ──close──▶ closed
  │                     │
  └──────cancel────▶ canceled
```

非法状态转移返回 `409 invalid_state_transition` 并附带当前状态与允许事件。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python · FastAPI · SQLAlchemy · Alembic |
| 数据 | PostgreSQL · pgvector（1536 维 HNSW） |
| 队列 | Redis · RQ（异步任务，Linux 容器运行 worker） |
| AI | ChatProvider / EmbeddingProvider 接口抽象；Fake + OpenAI-compatible 双实现 |
| 前端 | Vue 3 · TypeScript · Vite · nginx |
| 工程 | Docker Compose · pytest · GitHub Actions · GitHub Flow |

## 主要功能

- ✅ 注册 / 登录 / JWT / 三角色 RBAC（customer / agent / admin）与数据范围隔离
- ✅ 工单创建（幂等）、列表（分页 / 筛选 / 排序）、详情、更新、状态机
- ✅ AI 结构化输出：分类 / 摘要 / 优先级 / 情绪（Pydantic schema 校验 + 重试 + 降级）
- ✅ 知识库文档上传（PDF / TXT）、切片、Embedding、pgvector 检索、RAG 回复建议
- ✅ 人工审核 → 发送回复 → 关闭工单 → 客户评价闭环
- ✅ 全量审计日志（append-only）
- ✅ Redis + RQ 异步任务（稳定 job_id 幂等、失败重试）
- ✅ 工单创建 Idempotency-Key、RQ 任务幂等
- ✅ pytest（110+ 用例，0 网络依赖）+ GitHub Actions 自动测试 + Docker Compose 一键启动

## 明确不做（MVP 边界）

SSE 流式输出 · WebSocket · 多租户 · 微服务 · Kubernetes · 小程序 · 支付 · 短信/邮件 · 复杂统计大屏 · 通用幂等框架 · **AI 自动发送回复**

---

## 快速开始

> 前置：Docker Desktop；`.env` 从 `.env.example` 复制（API Key 只在本机配置，不入库）。
> 注意：本项目的 Postgres 宿主端口用 **5433**、后端用 **8001**，避免与项目一（Career Knowledge Copilot 占 5432 / 8000）冲突。

### Docker 一键启动（推荐）

```bash
cp .env.example .env
# 编辑 .env：DATABASE_URL / REDIS_URL / SECRET_KEY；默认 CHAT_PROVIDER=fake、EMBEDDING_PROVIDER=fake

docker compose up -d --build
```

四个服务（backend 与 worker 共用同一镜像，仅 CMD 不同）：

| 服务 | 说明 |
|---|---|
| `db` | PostgreSQL + pgvector（host 端口 5433） |
| `redis` | RQ 队列（host 端口 6379） |
| `backend` | FastAPI + uvicorn，启动时自动 `alembic upgrade head` |
| `worker` | RQ worker，监听 `default` 队列（运行在 Linux 容器，不在 Windows 宿主直接运行） |

```bash
docker compose ps          # 全部 healthy
docker compose logs -f worker   # 查看 worker 队列日志
docker compose down        # 停止（数据保留在 volume）
docker compose down -v     # 停止并清除数据卷
```

### 本地开发（不跑 Docker 服务时）

```bash
# 1. 准备数据库（Docker 方式启动 Postgres + Redis）
docker compose up -d db redis

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\activate           # Windows
pip install -r backend/requirements.txt

# 3. 迁移 + 启动 API
alembic upgrade head
uvicorn backend.app.main:app --reload --port 8001
```

> 本地直连数据库使用 `.env` 的 `DATABASE_URL`（指向 `localhost:5433`）；容器内由 Compose 覆盖为 `db:5432`。`backend/app/config.py` 只在缺失 `DATABASE_URL` 时抛错。

服务地址：

| 服务 | 地址 |
|---|---|
| 后端 API / Swagger | http://localhost:8001/docs |
| 健康检查 | http://localhost:8001/health |
| 数据库 | localhost:5433 |
| Redis | localhost:6379 |

### 运行测试

```bash
docker compose up -d db redis      # 测试需要数据库
.venv\Scripts\activate
python -m pytest -q
```

- 测试自动创建隔离的 `ai_workbench_test` 数据库，互不影响；
- 强制 Fake Provider，0 网络依赖；
- 覆盖表结构、CHECK 约束、唯一约束、外键级联删除、HNSW 向量索引。

## 当前进度

**W1-A 基础设施 + 数据模型 ✅（2026-08-09）**

- [x] Git 仓库（本地初始化）
- [x] FastAPI 后端骨架（config / database / main / health）
- [x] Docker Compose：db / redis / backend / worker 四服务（worker 与 backend 共用镜像）
- [x] 9 张表数据模型（users / tickets / ticket_replies / knowledge_items / knowledge_chunks / audit_logs / evaluations / ai_processing_jobs / idempotency_keys）
- [x] 首条 Alembic 迁移（含 `CREATE EXTENSION vector` + HNSW `vector_cosine_ops` 索引），upgrade / downgrade 往返验证通过
- [x] 数据库模型测试 19 个全绿
- [x] RQ worker 在 Linux 容器运行并监听 `default` 队列

**下一阶段（W1-B）**：注册 / 登录 / JWT / RBAC

## 开发流程

严格按 Issue → feature 分支 → 实现 → pytest → Swagger 验收 → PR → Code Review → 合并推进，详见 [docs/collaboration-workflow.md](docs/collaboration-workflow.md)。

---

## 文档目录

| 文档 | 内容 |
|---|---|
| [docs/PRD.md](docs/PRD.md) | 产品需求：用户、痛点、MVP 范围、页面、流程、AI 规则 |
| [docs/user-stories.md](docs/user-stories.md) | 按角色拆分的用户故事 |
| [docs/data-model.md](docs/data-model.md) | 9 张表：字段 / 类型 / 约束 / 索引 / 外键 / 删除策略 |
| [docs/api-design.md](docs/api-design.md) | 16+ 接口：方法 / 角色 / 参数 / 幂等 / 后台任务 |
| [docs/state-machine.md](docs/state-machine.md) | 工单状态机：状态、事件、角色、非法转移、审计 |
| [docs/technical-design.md](docs/technical-design.md) | 架构、Provider 抽象、结构化输出、RQ、幂等、安全 |
| [docs/test-cases.md](docs/test-cases.md) | 测试策略、用例清单、数量目标 |
| [docs/development-plan.md](docs/development-plan.md) | 3 周开发计划、每周验收与回退 |
| [docs/collaboration-workflow.md](docs/collaboration-workflow.md) | Issue / 分支 / PR / Review 协作规范 |
| [docs/resume-interview.md](docs/resume-interview.md) | 简历描述、亮点、10 个面试问答、边界与演进 |

---

## 里程碑

| 周 | 内容 | 目标 |
|---|---|---|
| W1 | 骨架 + 认证 + RBAC + 全表迁移 | pytest 20–30 绿 |
| W2 | 工单 + 状态机 + 审计 + 幂等 + Fake AI | pytest 60–80 绿 |
| W3 | RQ + 知识库 RAG + 建议 + 闭环 + CI | pytest ≥110 绿 |

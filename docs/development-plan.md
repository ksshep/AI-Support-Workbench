# 开发计划（3 周主线）

版本：v1.0 · 状态：Draft · 最后更新：2026-08-09

## 0. 总原则

- **主线 3 周，每周留 1 天 buffer**；哪周超支，只砍"延伸/增强"项，不砍主线。
- **每天真实开发 4–6 小时**（AI 辅助开发路线：AI 生成 → 读代码 → 测试 → 调试 → 总结）。
- 每个功能走完整流程：`Issue → feature 分支 → Codex 实现 → pytest → Swagger 验收 → diff 审查 → PR → Code Review → 合并`。
- 每个 PR 合入前必须 `pytest -q` 全绿 + `git diff --check` 通过。
- 测试用 Fake Provider；真实 Provider 只在每周末手工验收。

---

## 第一周（8/10 – 8/16）：骨架 + 认证 + RBAC

### 目标

跑起三服务 + Redis + worker；完成注册/登录/JWT/RBAC；建好全部表结构（迁移）。

### 修改文件

| 文件 | 内容 |
|---|---|
| `backend/app/main.py` | FastAPI 入口、CORS、health |
| `backend/app/database.py` | engine / SessionLocal / get_db |
| `backend/app/models.py` | users、tickets、ticket_replies、evaluations、knowledge_items、knowledge_chunks、audit_logs、ai_processing_jobs、idempotency_keys（全部模型） |
| `backend/app/security.py` | bcrypt / JWT |
| `backend/app/deps.py` | get_current_user / require_roles |
| `backend/app/schemas.py` | 认证相关 Pydantic |
| `backend/app/routers/auth.py` | register / login / me |
| `backend/app/routers/admin.py` | admin 用户管理（列表/创建/改角色） |
| `alembic/versions/0001…0005` | 迁移脚本 |
| `backend/requirements.txt` | fastapi/uvicorn/sqlalchemy/psycopg/alembic/pyjwt/passlib/bcrypt/rq/redis/fakeredis/pytest/httpx/pgvector/pypdf |
| `docker-compose.yml` | db / redis / backend / worker / frontend |
| `backend/Dockerfile` | 单镜像（web + worker 两 CMD） |
| `frontend/src/` | 登录页 / 注册页 / 路由守卫 / api.ts / auth store |
| `.github/workflows/ci.yml` | 基础版（仅 test job） |
| `.env.example` | 全部环境变量模板 |

### 预计产出

- `docker compose up` 一键启动，5 个服务健康；
- 注册 → 登录 → 鉴权全通；admin 可创建 agent/admin；
- `users` / `tickets` 等全部表建好，Alembic 可 `upgrade head` / `downgrade`；
- 越权（customer 调 admin 接口）返回 403；
- 第一份测试：auth + security + RBAC + 集成约束。

### 验收命令

```bash
docker compose up -d --build
docker compose ps            # 5 个服务 healthy
docker compose exec backend alembic current   # 显示 head
docker compose exec backend pytest -q         # 首轮测试全绿
```

### Swagger 验收步骤

```
1. 打开 http://localhost:8000/docs
2. POST /auth/register 注册 customer（200）
3. POST /auth/login 登录拿 token
4. 点击 Authorize 粘贴 Bearer token
5. GET /auth/me（200，role=customer）
6. POST /admin/users（无 admin token → 401；用 admin token → 201）
```

### 测试数量目标

20–30 个（auth、security、RBAC、集成约束）。

### 回退方案

- 迁移问题：`alembic downgrade -1` 后修复再 `upgrade head`；
- 服务起不来：检查 `docker compose logs backend`；多半是 `.env` 缺变量，补全即可；
- 若 Compose 全部失败：先 `docker compose down -v`，用 `.env.example` 重建 `.env` 再起。

---

## 第二周（8/17 – 8/23）：工单 + 状态机 + 审计 + Fake AI

### 目标

工单 CRUD / 状态机 / 分页筛选 / 审计全部落库；AI 结构化输出用 Fake Provider 打通（Pydantic 校验 + 落库）；幂等键实现。

### 修改文件

| 文件 | 内容 |
|---|---|
| `backend/app/routers/tickets.py` | 创建（幂等）/ 列表（分页筛选）/ 详情 / 更新 / transition |
| `backend/app/state_machine.py` | 显式转移表 + allowed_events |
| `backend/app/audit.py` | record_audit |
| `backend/app/idempotency.py` | Idempotency-Key 实现 |
| `backend/app/chat_provider.py` | ChatProvider + extract 方法 |
| `backend/app/embedding.py` | EmbeddingProvider（Fake） |
| `backend/app/ai_processor.py` | 结构化输出 → Pydantic 校验 |
| `backend/app/schemas.py` | TicketAnalysis / ReplySuggestion |
| `backend/app/routers/admin.py` | `/admin/tickets` 代建 |
| `backend/app/routers/replies.py` | 审核 / 发送 / 评价（骨架） |
| `tests/` | 工单 API / 状态机 / 审计 / 幂等 / schema 测试 |
| `frontend/src/` | 我的工单页 / 工单详情页 / 客服工作台骨架 |

### 预计产出

- 工单全流程 API 可走通（创建 → start_review → 发送回复 → 关闭），Fake AI 分析结果落库；
- 非法转移 409 + allowed_events；审计日志随写操作产生；
- 幂等键重复请求不重复建单；
- 分页 / 筛选 / 关键字搜索可用。

### 验收命令

```bash
docker compose exec backend pytest -q
docker compose exec backend alembic current   # 无 pending 迁移
```

### Swagger 验收步骤

```
1. customer token：POST /tickets（带 Idempotency-Key）→ 201
2. 同 key 再发 → 200 同 id；总列表数不变
3. GET /tickets?status=open&page=1 → items 分页正确
4. agent token：POST /tickets/{id}/transition {"event":"start_review"} → in_review
5. 再发 {"event":"close"} → 409 invalid_state_transition + allowed_events
6. 生成回复草稿（第二周为骨架，第三周接入 RQ）
7. GET /admin/audit-logs → 看到 ticket.created / ticket.status_changed
```

### 测试数量目标

第二周累计 60–80 个。

### 回退方案

- 状态机改错：只动 `state_machine.py` 一张表，单测覆盖即可快速定位；
- 幂等键引入脏数据：`idempotency_keys` 表可安全 truncate（无外键影响业务表）；
- 分页参数混乱：统一在 `schemas.py` 里定义 `PageQuery`，一处修改全局生效。

---

## 第三周（8/24 – 8/30）：RQ + 知识库 RAG + 建议 + 闭环 + 测试/CI

### 目标

AI/Embedding 全部进 RQ worker；知识库上传检索；回复建议生成；人工审核 → 发送 → 关闭 → 评价闭环；测试补全；CI + README。

### 修改文件

| 文件 | 内容 |
|---|---|
| `backend/app/tasks.py` | RQ 任务（ticket_analysis / knowledge_index / reply_suggestion）、稳定 job_id、Retry |
| `backend/app/rag.py` | 知识库检索 + RAG 上下文 |
| `backend/app/routers/knowledge.py` | 上传 / 列表 / 详情 / 删除 / search |
| `backend/app/routers/replies.py` | 建议生成（异步）/ 审核 / 发送 / 评价 完成 |
| `backend/app/routers/stats.py` | 工单统计 |
| `backend/app/worker.py` | worker 入口（加载 app context） |
| `backend/Dockerfile` | worker CMD |
| `tests/` | 知识库 / RAG / 任务（fakeredis）/ 端到端脚本 |
| `.github/workflows/ci.yml` | 完整版（test + docker-build） |
| `README.md` | 项目主页 |
| `frontend/src/` | 知识库页 / 审计页 / 用户管理页 / 统计卡 |

### 预计产出

- 创建工单 → RQ 分析 → 前端轮询看到结果；
- 知识库上传 → ready → 检索 → 建议生成；
- 客服工作台完成闭环：建议 → 审核 → 发送 → 关闭 → 客户评价；
- 审计日志覆盖全部写操作；RQ 失败可重试、任务幂等；
- CI 在 GitHub 自动跑 pytest + Docker 构建。

### 验收命令

```bash
docker compose up -d --build
docker compose exec backend pytest -q           # 全绿 ≥110
docker compose logs worker | tail -20            # 任务执行/重试日志
git diff --check
```

### Swagger 验收步骤（完整闭环）

```
1. customer 建单（幂等）→ 轮询 GET /tickets/{id}/analysis 直到 succeeded
2. admin 建 agent 账号 → agent 登录
3. agent：POST /knowledge 上传 txt → 轮询 GET /knowledge/{id} 到 ready
4. agent：POST /tickets/{id}/reply-suggestion → 202
5. 轮询详情 → 出现 is_ai_suggestion=true 草稿
6. agent：PATCH 审核草稿 → POST /tickets/{id}/replies 发送 → 状态 replied
7. agent：transition close → closed
8. customer：POST /tickets/{id}/evaluation → 201
9. admin：GET /admin/audit-logs 确认关键 6 条日志
10. 重复 POST /tickets 同 key → 不重复建单
11. 拔掉真实 Provider 配置 → 全程 Fake 也能走通（CI 依据）
```

### 测试数量目标

累计 **≥110**，全绿。

### 回退方案

- RQ 任务卡死：`docker compose restart worker`；先确认 `redis` 健康；
- 知识库向量维度不符：统一 `EMBEDDING_DIMENSION=1536`，测试库 ALTER 重置（复用项目一做法）；
- CI 在 GitHub 失败但本地过：多半是环境变量 / 服务启动时序，在 Actions 里加 `pg_isready` 等依赖等待；
- 时间不够：砍「统计卡前端」「知识库 search 前端页」，后端接口保留。

---

## 里程碑与验收

| 周 | 里程碑 | 验收 |
|---|---|---|
| W1 | 三服务 + Redis + worker 起；认证/RBAC 全通；全表迁移 | `pytest -q` 20–30 绿 |
| W2 | 工单全流程 + 状态机 + 审计 + 幂等 + Fake AI | `pytest -q` 60–80 绿 |
| W3 | RQ + RAG + 建议 + 闭环 + CI + README | `pytest -q` ≥110 绿；CI 绿 |

**里程碑没绿 → 不进下一步**：任何一周的测试不达标，宁可把下一周的功能延后，也要先补测试，因为测试是面试讲"工程能力"的底牌。

---

## 每日节奏（AI 辅助开发）

```
1. 选一个 Issue（小功能）
2. 建 feature 分支（命名规则见 `docs/collaboration-workflow.md`）
3. Codex/AI 生成或修改代码
4. 通读生成的代码，理解逻辑（边读边标注不理解处）
5. pytest 新增用例 + 全量回归
6. Swagger 手工验收
7. git diff 自审（对着 diff 讲一遍"为什么这么写"）
8. 提交 → 推送 → 开 PR → 自评 → 合并
```

---

## 风险与应对

| 风险 | 应对 |
|---|---|
| 第三周超支 | 第二周提前把 replies 路由骨架搭好；砍前端统计页 |
| RQ worker 环境问题 | worker 只在 Linux 容器跑，本地调试用 `docker compose exec worker pytest` |
| 真实 Provider 不稳定 | 测试全 Fake；手工验收时才配真实 Key，失败不影响主线 |
| 向量维度 / 检索不准 | 复用项目一已验证的 1536 + HNSW；验收脚本固定断言 |
| AI 结构化输出不稳定 | Pydantic 校验 + 2 次重试 + 最终降级为"AI 不可用但可人工处理" |

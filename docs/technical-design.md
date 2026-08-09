# 技术设计

版本：v1.0 · 状态：Draft · 最后更新：2026-08-09

## 1. 架构总览

```text
┌─────────────┐        ┌──────────────────────────────────────────────┐
│  浏览器      │  HTTPS  │  nginx（前端静态资源 + /api 反代）             │
│  Vue 3 SPA  │ ──────▶ │                                              │
└─────────────┘        │  ┌────────────────────────┐   ┌────────────┐  │
                       │  │  FastAPI（web，同步）    │   │  RQ Worker │  │
                       │  │  - 认证/RBAC            │   │  (Linux)   │  │
                       │  │  - 工单/回复/评价        │   │  - AI 分析  │  │
                       │  │  - 知识库上传            │   │  - Embedding│  │
                       │  │  - 审计查询              │   │  - 回复建议 │  │
                       │  │  - 入队 RQ               │   └─────┬──────┘  │
                       │  └──────────┬─────────────┘         │          │
                       └─────────────┼───────────────────────┼──────────┘
                                     │                       │ enqueue
                          ┌──────────┼──────────┐            │
                          ▼          ▼          ▼            ▼
                     ┌────────┐ ┌────────┐  ┌──────────────────┐
                     │Postgres│ │pgvector│  │  Redis (RQ 队列)  │
                     └────────┘ └────────┘  └──────────────────┘
```

- **FastAPI** 只做同步快操作（建单、审核、查询、入队），**所有 AI/Embedding 调用在 RQ Worker 中执行**。
- RQ Worker 运行在 **Docker Linux 容器**，Windows 宿主机不直接运行 worker。
- 数据库写操作在 `PostgreSQL`；向量检索用 `pgvector`；队列用 `Redis`（RQ）。

---

## 2. 技术栈与版本选型

| 组件 | 选型 | 理由 |
|---|---|---|
| Web 框架 | FastAPI | 与项目一一致，Pydantic 校验 + 依赖注入做 RBAC |
| ORM | SQLAlchemy 2.x | 与项目一一致 |
| 迁移 | Alembic | 与项目一一致 |
| 数据库 | PostgreSQL 17（pgvector 镜像） | 与项目一一致 |
| 向量 | pgvector（1536 维，HNSW） | 与项目一一致 |
| 缓存/队列 | Redis 7 | 队列 + 幂等键 TTL 预留 |
| 任务队列 | **RQ**（不用 Celery） | 单机单库足够；worker/queue 概念简单，便于面试讲清楚；Celery 留作演进 |
| 认证 | JWT（`python-jose` 或 `pyjwt`）+ bcrypt（`passlib`/`bcrypt`） | 无状态，SPA 友好 |
| 测试 | pytest + httpx + TestClient | 与项目一一致 |
| 前端 | Vue 3 + TS + Vite + nginx | 与项目一一致 |
| CI | GitHub Actions | 自动 pytest + Docker 构建校验 |
| 编排 | Docker Compose | 与项目一一致 |

---

## 3. 目录结构（预期）

```text
ai-support-workbench/
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI 入口
│   │   ├── database.py            # engine / SessionLocal / get_db
│   │   ├── models.py              # 全部 SQLAlchemy 模型
│   │   ├── schemas.py             # Pydantic 请求/响应 + AI 结构化输出
│   │   ├── security.py            # bcrypt / JWT
│   │   ├── deps.py                # get_current_user / require_roles
│   │   ├── chat_provider.py       # ChatProvider 抽象（复用项目一设计）
│   │   ├── embedding.py           # EmbeddingProvider 抽象（复用项目一设计）
│   │   ├── provider_factory.py    # 工厂：fake / compatible
│   │   ├── ai_processor.py        # 结构化输出 → Pydantic 校验 → 落库
│   │   ├── rag.py                 # 知识库检索 + RAG 上下文构建
│   │   ├── state_machine.py       # 显式转移表
│   │   ├── audit.py               # 审计写入
│   │   ├── idempotency.py         # 幂等键
│   │   ├── tasks.py               # RQ 任务定义（job_id / 重试）
│   │   ├── routers/
│   │   │   ├── auth.py
│   │   │   ├── tickets.py
│   │   │   ├── knowledge.py
│   │   │   ├── admin.py
│   │   │   └── stats.py
│   │   └── ...
│   ├── Dockerfile                 # 同一镜像跑 web + worker（不同 CMD）
│   └── requirements.txt
├── frontend/
│   ├── src/
│   ├── Dockerfile
│   └── nginx.conf
├── alembic/
│   └── versions/                  # 0001..0005
├── alembic.ini
├── tests/
├── docker-compose.yml
├── .github/workflows/ci.yml
├── .env.example
├── README.md
└── docs/
```

---

## 4. 认证与 RBAC

### 4.1 认证流程

1. `POST /auth/login` → bcrypt 校验密码 → 签发 JWT（payload：`{ sub: user_id, role, exp }`）。
2. 前端存 token（`localStorage`），请求带 `Authorization: Bearer <token>`。
3. 依赖 `get_current_user` 解析 token → 查 `users` → 校验 `is_active`。

### 4.2 RBAC 实现

```python
def require_roles(*roles: str) -> Callable:
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(403, "forbidden")
        return current_user
    return checker
```

- 路由声明：`tickets = APIRouter(dependencies=[Depends(require_roles("customer"))])`。
- 未认证 → `401`；认证但角色不符 → `403`。
- **数据范围隔离**：customer 只能查本人工单（查询强制 `customer_id == current_user.id`），不能只靠角色。

### 4.3 密码与密钥

- 密码：bcrypt（12 轮默认）。
- JWT 密钥：`SECRET_KEY` 环境变量，不在仓库。
- `access_token` 有效期：`ACCESS_TOKEN_EXPIRE_MINUTES`（默认 60 分钟）。

---

## 5. ChatProvider / EmbeddingProvider 抽象

与项目一完全一致的抽象风格，直接复用设计思想（不复制文件）：

```python
class ChatProvider(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str: ...

    @abstractmethod
    def extract(self, system_prompt: str, user_prompt: str,
                schema: type[BaseModel]) -> dict: ...
    # extract：要求模型输出符合 Pydantic schema 的 JSON，返回 dict（结构化输出）

class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

class FakeChatProvider(ChatProvider):   # 确定性，无网络
class OpenAICompatibleChatProvider(ChatProvider):  # 通过环境变量配置

class FakeEmbeddingProvider(EmbeddingProvider):    # 确定性，无网络
class OpenAICompatibleEmbeddingProvider(EmbeddingProvider): # 环境变量配置

# 工厂
def get_chat_provider() -> ChatProvider:
    if os.getenv("CHAT_PROVIDER", "fake") == "compatible":
        return OpenAICompatibleChatProvider.from_env()
    return FakeChatProvider()
```

### 5.1 环境变量（全部从 env 读取）

| 变量 | 用途 |
|---|---|
| `CHAT_PROVIDER` | `fake` / `compatible` |
| `CHAT_BASE_URL` / `CHAT_API_KEY` / `CHAT_MODEL` / `CHAT_TIMEOUT_SECONDS` | 真实 Chat |
| `EMBEDDING_PROVIDER` | `fake` / `compatible` |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_TIMEOUT_SECONDS` | 真实 Embedding |
| `DATABASE_URL` / `REDIS_URL` / `SECRET_KEY` / `ACCESS_TOKEN_EXPIRE_MINUTES` | 系统 |
| `RQ_MAX_RETRIES` / `RQ_RETRY_INTERVAL_SECONDS` | 重试 |

> 规则：Provider 构造函数内校验缺失配置并抛 `ConfigError`；API Key、Base URL、模型一律来自 env，**业务代码不写死厂商名**。

---

## 6. AI 结构化输出设计（Pydantic schema）

### 6.1 输出 schema

```python
class TicketAnalysis(BaseModel):
    """AI 对工单的结构化分析结果。"""
    category: str                # 分类标签
    summary: str                 # 一句话摘要
    priority: Literal["low", "normal", "high", "urgent"]
    sentiment: Literal["positive", "neutral", "negative"]
    confidence: float            # 0.0 ~ 1.0
    reason: str                  # 判断依据说明

class ReplySuggestion(BaseModel):
    """AI 回复建议（不直接发送）。"""
    reply_content: str           # 建议回复正文
    citations: list[str]         # 引用的知识库片段（可空）
    confidence: float            # 0.0 ~ 1.0
```

### 6.2 字段约束

| 字段 | 约束 | 说明 |
|---|---|---|
| `category` | `str`，`min_length=1`，`max_length=50` | 规范化标签（`billing`、`account`、`technical`、`product`、`other`） |
| `summary` | `str`，`min_length=1`，`max_length=500` | 摘要 |
| `priority` | `Literal[...]` 四选一 | 枚举值：low/normal/high/urgent |
| `sentiment` | `Literal[...]` 三选一 | 枚举值：positive/neutral/negative |
| `confidence` | `float`，`ge=0.0`，`le=1.0` | 置信度 |
| `reason` | `str`，`max_length=500` | 判断依据 |

### 6.3 枚举值定义

```python
PRIORITIES = ("low", "normal", "high", "urgent")
SENTIMENTS = ("positive", "neutral", "negative")
# category 为自由文本但建议限制在固定集合内，见 prompt 约束
```

### 6.4 缺失字段 / 无效值处理

1. 模型输出先做 `json.loads`；解析失败 → 进入重试逻辑；
2. 再用 `TicketAnalysis.model_validate(obj)` 校验；
3. **校验失败**：统一抛 `StructuredOutputError`，记录原始输出到 job `error_message`，按 RQ 重试；
4. 失败超限后：`ticket_analysis` 任务将工单状态保留 `open`（AI 字段保持默认/空），前端显示"AI 分析暂不可用"，**不影响人工处理流程**。

### 6.5 JSON 解析失败处理

```python
try:
    raw = json.loads(provider_raw)
except (json.JSONDecodeError, TypeError):
    raise StructuredOutputError("Model returned invalid JSON") from None
try:
    parsed = TicketAnalysis.model_validate(raw)
except ValidationError as exc:
    raise StructuredOutputError(f"Model output failed schema: {exc}") from None
```

### 6.6 不符合 schema 时的重试

- **Prompt 层**：要求模型只输出 JSON，并给示例；
- **结构化层**：真实 provider 用 JSON mode / function calling 提高命中率；fake provider 直接返回合法 JSON；
- **重试策略**：单次失败 → 重试 **2 次**（总 3 次尝试），每次重新调用 provider 并再次校验；重试间隔 RQ `retry` 配置；
- **最终失败**：job `failed`，写入 `error_message`，不再消耗 token。人工流程可继续。

---

## 7. RQ 后台任务设计

### 7.1 三类任务

| 任务 | 触发 | job_id | queue | 重试 | 超时 | 失败记录 |
|---|---|---|---|---|---|---|
| 工单 AI 分析 `ticket_analysis` | 创建工单后自动入队 | `ticket_analysis:{ticket_id}` | `default` | 3 次（含首次） | 120s | `ai_processing_jobs.error_message` |
| 知识库 Embedding `knowledge_index` | 上传文档后入队 | `knowledge_index:{item_id}` | `default` | 3 次 | 300s | `knowledge_items.error_message`（复用项目一 status 模式） |
| 回复建议生成 `reply_suggestion` | 客服点击"生成建议"入队 | `reply_suggestion:{ticket_id}` | `default` | 3 次 | 120s | `ai_processing_jobs.error_message` |

> 单一 `default` queue 足够 MVP；多 queue（`analysis` / `embedding`）留作演进。这样面试也能回答"为什么要多队列"。

### 7.2 入队方式（稳定 job_id）

```python
from redis import Redis
from rq import Queue
from rq.job import Job

redis = Redis.from_url(os.getenv("REDIS_URL"))
q = Queue("default", connection=redis)

def enqueue_ticket_analysis(ticket_id: str) -> Job:
    job_id = f"ticket_analysis:{ticket_id}"      # 稳定业务 key
    job = q.fetch_job(job_id)
    if job is not None and job.status in ("queued", "started"):
        return job                                # 已存在，不重复入队
    return q.enqueue(process_ticket_analysis, ticket_id,
                     job_id=job_id,
                     retry=Retry(max=2, interval=[5, 30]))
```

### 7.3 幂等（三层防线）

1. **RQ job_id 稳定 key**：`ticket_analysis:{ticket_id}` 已排队/运行则跳过；
2. **数据库唯一约束**：`UNIQUE (ticket_id, job_type)`，插入冲突即跳过；
3. **任务函数内先查状态**：`succeeded` 直接返回，`processing` 且未超时跳过。

### 7.4 失败重试

- `Retry(max=2, interval=[5, 30])`：失败后 5s、30s 各重试一次。
- 每次失败记录 `attempts+1`、`last_error_at`、`error_message`。
- 终态失败：job `failed`；工单仍可人工处理（AI 字段为默认值）。

### 7.5 前端如何查询任务状态

- 前端**不直接连 Redis/RQ**，统一走 REST：
  - `GET /tickets/{id}` 响应内含 `ai_job`（`status` / `result` / `error_message`）；
  - 知识库：`GET /knowledge/{id}` 的 `status`；
  - 前端轮询（3s 间隔）详情接口，直到 `succeeded` / `failed`。
- 不做 SSE/WebSocket（明确不做），轮询即可满足 MVP。

### 7.6 取消策略（工单 canceled 后）

- 若 `ticket_analysis` 仍在队列：`q.fetch_job(job_id)` → `job.cancel()`；
- 已开始的 job 不中断，worker 写完结果后写入 job 记录，但工单已是终态，忽略结果。

---

## 8. 知识库与 RAG（复用项目一设计思想）

- 上传 → `knowledge_items`（processing）→ 入队 `knowledge_index`：
  - TXT：直接读全文；PDF：`pypdf` 提取全文；
  - 切片：复用项目一的 `split_text_into_chunks` 思路（按段落/长度切分）；
  - Embedding：`EmbeddingProvider.embed_texts` → 写入 `knowledge_chunks.embedding`（1536 维）；
  - 状态 → `ready` / `failed`（写 `error_message`）。
- 检索：`POST /knowledge/search`（内部验证）与 RAG 共用 `vector_search`：
  ```sql
  SELECT kc.id, kc.content, kc.knowledge_item_id, ki.title,
         1 - (kc.embedding <=> :query_vec) AS similarity
  FROM knowledge_chunks kc JOIN knowledge_items ki ON ki.id = kc.knowledge_item_id
  WHERE ki.status = 'ready'
  ORDER BY kc.embedding <=> :query_vec
  LIMIT :top_k
  ```
- 回复建议流程：
  1. 取工单描述（+ 分类/摘要）embedding；
  2. 检索 top_k=5；
  3. 构建 RAG 上下文（含引用标题与内容）；
  4. `ReplySuggestion` 结构化输出；
  5. 创建 `ticket_replies(is_ai_suggestion=true, is_sent=false)` 草稿；
  6. 无检索依据 → 建议内容为空，前端提示。

---

## 9. 幂等设计

### 9.1 工单创建幂等（Idempotency-Key 头）

1. 客户端生成 UUID 作为 `Idempotency-Key` 请求头；
2. 服务端：查 `idempotency_keys`（key + actor_id）→ 命中则返回快照（`200`）；
3. 未命中：`INSERT idempotency_keys` → 创建工单 → `INSERT tickets` → 同事务提交；
4. 任一写失败 → 整体回滚（幂等键一并回滚，允许重试）；
5. `request_hash` 防同 key 不同 body → `409 conflict`。

### 9.2 RQ 幂等

见 7.3。

### 9.3 明确不做

- 不做通用幂等框架（POST 全局幂等）；
- 只对 `POST /tickets`（Idempotency-Key）和 RQ 任务做幂等。

---

## 10. 审计日志

- 统一 `audit.py` 提供 `record_audit(db, actor, action, entity_type, entity_id, old, new, ip)`；
- 在**同一事务**内随业务写提交（保证业务成功才有审计，失败无日志）；
- `audit_logs` append-only，无 UPDATE/DELETE 接口；
- 后台任务写入的审计 `actor_id=NULL`。

---

## 11. 安全清单

| 项 | 措施 |
|---|---|
| 密码 | bcrypt，不存明文 |
| 密钥 | `SECRET_KEY`、API Key 只进 `.env`，`.env` 在 `.gitignore` |
| 认证 | JWT Bearer，`get_current_user` 校验 `is_active` |
| 越权 | `require_roles` + 数据范围过滤（customer 本人工单） |
| 注入 | SQLAlchemy 参数化；不用字符串拼 SQL |
| CORS | 仅配置允许的 `FRONTEND_ORIGIN` |
| 上传 | 类型白名单（pdf/txt）+ 20MB 限制 + 文件名清洗 |
| 日志 | 不记录密码、token 明文 |
| 输入 | Pydantic 全量校验 |

---

## 12. Docker / CI

### 12.1 docker-compose 服务

| 服务 | 镜像/构建 | 说明 |
|---|---|---|
| `db` | `pgvector/pgvector:pg17` | 健康检查 `pg_isready` |
| `redis` | `redis:7-alpine` | 队列 |
| `backend` | `backend/Dockerfile` | CMD：`alembic upgrade head && uvicorn` |
| `worker` | `backend/Dockerfile`（同镜像） | CMD：`rq worker default`；`depends_on: backend` |
| `frontend` | `frontend/Dockerfile` | nginx 托管 + `/api` 反代 |

> worker 与 backend 共用镜像、不同 CMD——一次构建，两处运行，依赖版本天然一致。

### 12.2 GitHub Actions（ci.yml）

```yaml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres: pgvector/pgvector:pg17
      redis: redis:7-alpine
    steps:
      - checkout
      - setup-python 3.13
      - pip install -r backend/requirements.txt
      - alembic upgrade head
      - pytest -q
  docker-build:
    runs-on: ubuntu-latest
    steps:
      - docker build backend
      - docker build frontend
```

### 12.3 测试基础设施

- 复用项目一模式：`.env` 的 `DATABASE_URL` → 建 `*_test` 库 → 强制 `CHAT_PROVIDER=fake`、`EMBEDDING_PROVIDER=fake` → `Base.metadata.create_all`。
- 测试不依赖 Redis：RQ 任务用 `fake_redis`（`fakeredis`）或跳过入队；AI 全用 Fake Provider。

---

## 13. 性能与边界（明确）

- 无限流（预留 429）；
- 无多租户；
- 无 K8s / 微服务；
- 单库单 Redis；
- 上传限制 20MB；
- 向量库 MVP 数据量 ≤ 几千条 chunk，HNSW 足够。

---

## 14. 演进方向（面试表达）

- SSE 流式输出；
- Celery + 多队列 + 定时任务（替代 RQ）；
- 多租户（tenant_id 全表加列）；
- 限流 / 缓存热数据（Redis）；
- 分类字典表（替代自由文本 category）；
- 知识库版本管理与向量增量更新。

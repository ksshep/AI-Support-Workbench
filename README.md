# AI Support Workbench

面向企业客服团队的 AI 工单协同工作台。

客户提交工单后，系统异步完成 AI 分析，从企业知识库检索依据并生成回复草稿；客服人工审核后发送，客户查看结果并评价。AI 只能生成草稿，不能绕过人工审核自动发送。

## 产品能力

- 客户端：提交工单、查看已发送回复、取消开放工单、评价已关闭工单。
- 客服工作台：分页筛选工单、查看 AI 分析、审核/退回/发送回复、关闭工单。
- 管理后台：上传 PDF/TXT 知识库、查看处理状态、检索和删除知识库内容。
- 工程能力：JWT、RBAC、状态机、幂等、审计、RQ 异步任务、pgvector 和 Docker Compose。

## 核心业务闭环

~~~text
客户提交工单
  -> 工单持久化 + Idempotency-Key
  -> RQ 异步 AI 分析
  -> 分类 / 摘要 / 优先级 / 情绪
  -> 知识库解析 / 切片 / Embedding / pgvector 检索
  -> 生成带来源的回复草稿
  -> 客服人工审核
  -> 发送回复
  -> 关闭工单
  -> 客户评价
~~~

回复生命周期由数据库状态约束：

~~~text
draft -> reviewed -> sent
~~~

工单状态机：

~~~text
open --start_review--> in_review --reply--> replied --close--> closed
  \\
   +----------------------cancel-------------------------> canceled
~~~

状态不能通过普通字段更新绕过状态机，非法事件由后端统一拒绝。

## 架构

~~~mermaid
flowchart TB
    Browser[Vue 3 Workbench] --> Nginx[Nginx]
    Nginx --> API[FastAPI]
    API --> DB[(PostgreSQL + pgvector)]
    API --> Redis[(Redis)]
    Redis --> Worker[RQ Worker]
    Worker --> DB
    Worker --> Chat[ChatProvider]
    Worker --> Embedding[EmbeddingProvider]
~~~

| 组件 | 职责 |
|---|---|
| Vue/Nginx | 登录、路由权限、客户页、客服工作台、知识库页 |
| FastAPI | 认证、RBAC、工单、回复、知识库、检索和评价 API |
| Service 层 | 状态机、幂等、审计、权限和事务边界 |
| PostgreSQL | 业务数据、任务数据、审计和评价 |
| pgvector | Chunk 向量存储和余弦相似度检索 |
| Redis/RQ | AI 分析、知识库摄取和回复建议后台任务 |
| Provider 工厂 | 根据环境变量选择 Chat/Embedding Provider |

## 关键设计

### AI 安全边界

1. AI 回复建议只能创建 draft。
2. draft 必须经过 agent/admin 审核。
3. 只有 reviewed 回复允许发送。
4. customer 只能看到 sent 回复。
5. RAG 引用来自实际检索结果，不伪造文件名、页码或 Chunk。
6. AI 失败会记录任务错误，不破坏原工单数据。

### 幂等与一致性

- 工单创建支持 Idempotency-Key。
- RQ 任务使用稳定 job_id。
- 数据库唯一约束防止重复任务和重复评价。
- 回复发送、工单状态流转和审计日志在同一事务中提交。
- 事务失败会 rollback，避免出现半完成状态。

### Provider 抽象

业务代码不依赖具体供应商：

~~~text
业务服务
  -> ChatProvider / EmbeddingProvider 接口
  -> Provider 工厂
  -> 环境变量选择平台和模型
~~~

支持 fake 和 OpenAI-compatible 两类 Provider。Chat 与 Embedding 分开配置，非兼容平台通过新增 Adapter 接入。

## API 概览

| 模块 | 主要接口 |
|---|---|
| Auth | POST /auth/register、POST /auth/login、GET /auth/me |
| Tickets | POST/GET /tickets、GET/PATCH /tickets/{id} |
| Workflow | POST /tickets/{id}/transition |
| Replies | POST/GET /tickets/{id}/replies |
| Review | POST .../review、POST .../send |
| AI Analysis | POST/GET /tickets/{id}/ai-analysis |
| Suggestions | POST/GET /tickets/{id}/reply-suggestions |
| Knowledge | POST/GET /knowledge-items、GET/DELETE /knowledge-items/{id} |
| Retrieval | POST /knowledge-search |
| Evaluation | POST/GET /tickets/{id}/evaluation |
| System | GET /health |

完整请求体、响应、角色和错误码见 [docs/api-design.md](docs/api-design.md)。

## 技术栈

FastAPI、Python、SQLAlchemy 2、Alembic、PostgreSQL、pgvector、Redis、RQ、Vue 3、TypeScript、Vite、Nginx、Docker Compose、pytest。

## 快速启动

要求：Docker Desktop 和 Git。

### Git Bash

~~~bash
cd /e/gz021/code/ai-support-workbench
cp .env.example .env
docker compose up -d --build
docker compose ps
~~~

### PowerShell

~~~powershell
Set-Location E:\gz021\code\ai-support-workbench
Copy-Item .env.example .env
docker compose up -d --build
docker compose ps
~~~

默认地址：

- Vue 工作台：http://localhost:5173
- Swagger：http://localhost:8001/docs
- 健康检查：http://localhost:8001/health
- PostgreSQL：localhost:5433
- Redis：localhost:6379

停止服务但保留数据：

~~~bash
docker compose down
~~~

删除服务和本地数据库卷：

~~~bash
docker compose down -v
~~~

## Provider 配置

本地测试和演示：

~~~dotenv
CHAT_PROVIDER=fake
EMBEDDING_PROVIDER=fake
~~~

OpenAI-compatible 服务：

~~~dotenv
CHAT_PROVIDER=compatible
CHAT_API_KEY=your-chat-api-key
CHAT_BASE_URL=https://example.com/v1
CHAT_MODEL=your-chat-model
CHAT_TIMEOUT_SECONDS=30

EMBEDDING_PROVIDER=compatible
EMBEDDING_API_KEY=your-embedding-api-key
EMBEDDING_BASE_URL=https://example.com/v1
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_DIMENSION=1536
EMBEDDING_TIMEOUT_SECONDS=30
~~~

更换 Embedding 模型时，必须确认真实维度、创建 Alembic 迁移、修改向量列并重新生成历史向量。API Key 只能保存在本地 .env，不能提交到 Git、测试、日志、截图或视频。

## 测试与验收

~~~bash
docker compose up -d db redis
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
python -m pytest -q
npm --prefix frontend run build
git diff --check
docker compose config --quiet
~~~

测试覆盖认证、RBAC、工单 CRUD、状态机、幂等、审计、回复审核发送、评价并发、Provider 异常、向量维度、RQ 任务、知识库摄取和 RAG 回复建议。

## 文档与演示

- [docs/PRD.md](docs/PRD.md)：产品需求与 MVP 范围
- [docs/data-model.md](docs/data-model.md)：数据模型、索引和约束
- [docs/api-design.md](docs/api-design.md)：API 设计
- [docs/state-machine.md](docs/state-machine.md)：状态机和角色矩阵
- [docs/technical-design.md](docs/technical-design.md)：技术架构、安全和任务设计
- [docs/test-cases.md](docs/test-cases.md)：测试策略和验收用例
- [docs/collaboration-workflow.md](docs/collaboration-workflow.md)：Issue、分支和 PR 流程
- [docs/resume-interview.md](docs/resume-interview.md)：项目描述和面试问答
- [PRODUCT.md](PRODUCT.md)：产品说明
- [DESIGN.md](DESIGN.md)：前端交互设计
- [docs/demo/ai-support-workbench-demo.mp4](docs/demo/ai-support-workbench-demo.mp4)：脱敏演示视频

## 生产化边界

这是一个可运行、可验收的企业 AI 应用 MVP。进一步生产化时可增加用户邀请、密码重置、登录限流、多租户隔离、对象存储、任务监控、集中日志、CI/CD、SSE/WebSocket 和统计报表。

## 开发协作

~~~text
Issue -> feature 分支 -> 实现 -> 测试/build
      -> Swagger/手工验收 -> Pull Request -> Review -> 合并
~~~

详细规则见 [docs/collaboration-workflow.md](docs/collaboration-workflow.md)。

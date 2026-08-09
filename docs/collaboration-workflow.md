# 国内真实开发流程（协作规范）

版本：v1.0 · 状态：Draft · 最后更新：2026-08-09

> 目标：让项目二在 GitHub 上**完整复刻国内团队的真实协作形态**——Issue 驱动、分支隔离、PR + Code Review、CI 把关。这条流程本身是面试的重要素材。

---

## 1. 标准协作流水线

```text
需求确认（读 PRD / 评审）
→ 创建 Issue（模板：背景 / 验收标准 / 关联）
→ 从 main 建 feature 分支（命名规则见 §4）
→ Codex / AI 实现一个小功能（一次只做一件事）
→ pytest 新增 + 回归
→ Swagger 手工验收
→ git diff 自审（对着 diff 讲一遍"为什么这么写"）
→ 提交并推送分支
→ 打开 Pull Request（关联 Issue，勾选自查清单）
→ Code Review（自审 + 有条件让同学/AI 互审）
→ 合并 main（squash）
→ 删除已合并分支
```

---

## 2. 仓库与保护规则

| 项 | 配置 |
|---|---|
| 默认分支 | `main` |
| 分支保护（GitHub） | 要求 PR 通过后才可合并；要求 CI 通过（pytest job）；禁止直接 push main |
| PR 标题规范 | `类型(范围): 描述`，如 `feat(tickets): add pagination and filters` |
| 提交信息规范 | `<类型>(<范围>): <描述>`；类型：feat / fix / refactor / test / docs / chore |
| 合并方式 | Squash merge（保持 main 历史线性） |

### Issue 模板

```markdown
## 背景
（为什么做这件事）

## 需求要点
- ...

## 验收标准
- [ ] 接口返回 201 且幂等键生效
- [ ] pytest 新增用例通过

## 关联
- 依赖 Issue：#
```

### PR 模板（自查清单）

```markdown
## 变更
（一句话说明改了什么）

## 关联 Issue
closes #12

## 自查
- [ ] pytest -q 全绿
- [ ] git diff --check 通过
- [ ] 涉及权限：正/反用例齐全
- [ ] 涉及状态机：非法转移用例齐全
- [ ] 未引入真实 API Key / 密码 / 个人信息
```

---

## 3. 建议的 Issue 列表（按依赖排序）

| # | Issue | 里程碑 | 预估 | 类型 |
|---|---|---|---|---|
| 1 | 项目骨架：三服务 + Redis + worker + Compose | W1 | 1 天 | chore |
| 2 | 数据模型 + Alembic 迁移（全表） | W1 | 1 天 | feat |
| 3 | 用户注册 / 登录 / JWT | W1 | 1 天 | feat |
| 4 | RBAC：require_roles + 数据范围过滤 | W1 | 0.5 天 | feat |
| 5 | admin 用户管理（创建 / 改角色） | W1 | 0.5 天 | feat |
| 6 | 工单创建（幂等键） | W2 | 1 天 | feat |
| 7 | 工单列表（分页 / 筛选 / 排序） | W2 | 0.5 天 | feat |
| 8 | 工单详情 + 更新 | W2 | 0.5 天 | feat |
| 9 | 工单状态机 + transition 接口 | W2 | 1 天 | feat |
| 10 | 审计日志写入 + 查询接口 | W2 | 1 天 | feat |
| 11 | AI 结构化输出：TicketAnalysis schema + Fake | W2 | 1 天 | feat |
| 12 | ChatProvider / EmbeddingProvider 抽象 + 工厂 | W2 | 0.5 天 | feat |
| 13 | RQ 任务：ticket_analysis（稳定 job_id + 重试 + 幂等） | W3 | 1 天 | feat |
| 14 | 知识库上传 / 切片 / Embedding / 状态 | W3 | 1.5 天 | feat |
| 15 | 知识库检索 + RAG 上下文 | W3 | 1 天 | feat |
| 16 | 回复建议生成（异步）+ 建议草稿 | W3 | 1 天 | feat |
| 17 | 审核 / 发送 / 关闭 / 评价闭环 | W3 | 1 天 | feat |
| 18 | 工单统计接口 | W3 | 0.5 天 | feat |
| 19 | GitHub Actions CI（test + docker-build） | W3 | 0.5 天 | chore |
| 20 | README + 验收脚本 | W3 | 0.5 天 | docs |

> 原则：**一个 Issue = 一次小改动 = 一个 PR**，避免大 PR 不好 review。Issue 编号就是分支名一部分（见 §4）。

---

## 4. 分支命名规则

```text
feature/<issue-no>-<kebab-case-描述>
```

示例：

| Issue | 分支 |
|---|---|
| #3 用户注册登录 | `feature/3-auth-register-login` |
| #6 工单创建幂等 | `feature/6-ticket-create-idempotency` |
| #9 状态机 | `feature/9-ticket-state-machine` |
| #13 RQ 分析任务 | `feature/13-rq-ticket-analysis` |
| #19 CI | `chore/19-ci-pipeline` |

- 分支一律从最新 `main` 切出；
- 提交前先 `git pull --rebase origin main`；
- 分支合并后删除本地 + 远端分支，保持仓库整洁。

---

## 5. Code Review 关注点（自审清单）

| 维度 | 问自己 |
|---|---|
| 正确性 | 边界条件（空输入、空列表、终态）是否处理 |
| 安全 | 权限是否校验；数据范围是否过滤；是否有密钥泄漏 |
| 一致性 | 是否复用已有抽象（Provider / 审计 / 分页） |
| 幂等 | 重复请求 / 重复任务是否被兜住 |
| 测试 | 正例 + 反例（403 / 409）是否都有 |
| 简洁 | 有没有复制粘贴的重复代码可以抽公共函数 |

> 面试提示：把 2–3 个 PR 的 review 意见截图或文字留在 README 的"协作流程"章节，展示真实的代码审查痕迹。

---

## 6. GitHub Actions 流程

```text
push / PR → ci.yml
├── job: test
│     ├── services: pgvector/postgres + redis
│     ├── alembic upgrade head
│     └── pytest -q（全 Fake Provider，0 网络依赖）
└── job: docker-build
      ├── docker build backend
      └── docker build frontend
```

- 任何 PR 未通过 CI 不可合并（分支保护强制）；
- `.env` 不入库，CI 用 `.env.example` + 测试环境变量。

---

## 7. 常见坑

| 坑 | 应对 |
|---|---|
| 直接往 main push | 分支保护强制 PR |
| 一个 PR 改 20 个文件 | 拆小：一个 Issue 一件事 |
| 提交信息含糊 | 用模板 + 规范检查 |
| 把 `.env` 提交 | `.gitignore` 先配好 + 提交前 `git status` 检查 |
| 测试依赖真实网络 | conftest 强制 Fake Provider |
| Review 拖延 | 自审为主，PR 当天开当天合 |

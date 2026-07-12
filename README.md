# Multi-Agent Workflow Orchestration System

企业级多智能体工作流编排系统，基于 LangGraph + FastAPI 构建。系统能够接收用户的自然语言请求，自动判断任务类型，并通过多个专业 Agent 协作完成任务。

> 核心设计理念：**多Agent系统的本质是任务编排系统，不是多个模型互相聊天。**

## 核心特性

- **智能路由网关** — Gateway 使用轻量模型自动识别用户意图，将请求分类为即时任务、项目型任务或拦截恶意请求
- **多 Agent 协作** — PM Agent 负责项目拆解与质量审查，Worker Agent（Analyzer / Coder / Tester）负责具体执行
- **两层编排架构** — 基于 LangGraph 有向图实现 Gateway → PM → Worker → Review 的闭环协作
- **SSE 流式执行追踪** — 通过 `POST /api/v1/chat/stream` 实时推送每个节点的执行进度，前端可展示实时进度时间线
- **显式状态机** — 7 种任务状态（TODO / DOING / REVIEW / DONE / FAILED / BLOCKED / HUMAN_PENDING），带合法转换校验
- **结构化验收标准** — PM 对照 `acceptance_criteria` 逐条检查 Worker 产物，拒绝"凭感觉判断"
- **失败打回重试** — PM 验收不通过可打回 Worker 重新执行，超限自动升级为人工介入
- **人工介入恢复** — 提供 `PATCH /tasks/{task_id}` 接口，解决 `HUMAN_PENDING` 任务卡死问题
- **Prompt 数据库化管理** — 所有 Agent Prompt 存储在 PostgreSQL 中，支持运行时热更新、版本管理和在线编辑，启动时自动种子填充默认值（设计参考 agency-agents 社区）
- **Prompt 注入防护** — 双层防线：规则匹配（零成本）+ Agent 间结构化消息边界
- **多租户隔离** — 所有请求携带 `tenant_id`，数据库按租户过滤
- **完整 Trace 链路** — 每次 Agent 调用记录 span_id、延迟、Token 消耗，支持审计与成本分析
- **模型分层策略** — 通过 OpenRouter 接入开源模型，路由用小模型（低延迟），PM/Worker 用强模型（质量优先）
- **LLM 连接测试** — 管理端提供 `POST /api/v1/test-llm` 接口，可在线测试模型连通性
- **前后端路由分离** — C 端用户页面（`/`）与管理控制台（`/admin`）独立 SPA，基于 Hash 路由

## 系统架构

```
用户请求
  │
  ▼
┌─────────────────────────────────────────────────────┐
│  FastAPI 接口层  (api/routes.py + main.py)           │
│  POST /api/v1/chat  ── 统一入口                      │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  Gateway 路由层  (gateway/router.py)                  │
│  1. Prompt注入检测（规则匹配）                         │
│  2. 意图分类（轻量模型，可配置）                        │
│     → instant（即时任务）                             │
│     → project（项目型任务）                           │
│     → blocked（拦截恶意请求）                          │
│     → scheduled（定时任务，MVP暂不支持）                │
└───────┬─────────────────┬──────────────┬────────────┘
        │                 │              │
        ▼                 ▼              ▼
   instant_handler   project_init   blocked_handler
   (单Worker执行)    (PM拆解+多Worker) (拒绝请求)
        │                 │              │
        │            ┌────┴────┐         │
        │            ▼         │         │
        │      worker_execute  │         │
        │            │         │         │
        │            ▼         │         │
        │        pm_review     │         │
        │        ┌───┴───┐    │         │
        │        │       │    │         │
        │      通过    打回   │         │
        │        │    ┌──┘    │         │
        │        │    ▼       │         │
        │        │  重试(≤3次) │         │
        │        │    │       │         │
        │        │  超限→人工  │         │
        │        ▼    ▼       │         │
        │     advance_task    │         │
        │        │            │         │
        │   还有任务→继续      │         │
        │   无任务→project_finalize     │
        │                             │
        ▼                             ▼
      END ←────────────────────────── END
```

### 关键组件

| 组件 | 文件路径 | 职责 |
|------|---------|------|
| **Gateway 路由** | `gateway/router.py` | 意图识别、Prompt 注入过滤、路由决策 |
| **身份提取** | `gateway/auth.py` | 租户/用户身份提取、请求上下文构建 |
| **PM Agent** | `agents/pm_agent.py` | 任务拆解、验收评审、失败处理 |
| **Worker Agent** | `agents/analyzer.py` 等 | 执行具体领域任务，输出结构化结果 |
| **状态图编排** | `graph/workflow.py` + `graph/nodes.py` + `graph/conditions.py` + `graph/state.py` | LangGraph 状态图，控制整体流转 |
| **持久化层** | `store/pg_store.py`（Facade）+ `store/project_store.py` + `store/task_store.py` + `store/trace_store.py` + `store/prompt_store.py` + `store/ddl.py` | PostgreSQL 异步 CRUD（asyncpg 连接池），按领域拆分子 Store |
| **Prompt 管理** | `prompt_loader.py`（PromptLoader 类）+ `defaults/prompts.py` | 运行时 Prompt 加载（依赖注入）、版本管理、种子填充 |
| **接口层** | `api/routes.py` + `api/schemas.py` | REST API 端点（含 Prompt 管理），Schema 定义独立文件 |
| **C 端页面** | `index.html` | 用户对话、项目看板、Trace 日志 |
| **管理控制台** | `admin.html` | Agent 管理、Prompt 编辑、模型配置、系统设置 |

## 项目结构

```
multi-agent/
├── pyproject.toml                    # 项目配置与依赖声明
├── .env.example                      # 环境变量模板
├── index.html                        # C端用户 SPA（对话/项目/Trace）
├── admin.html                        # 管理控制台 SPA（仪表盘/Agent/Prompt/模型等）
├── data/                             # 数据文件目录
├── scripts/                          # 脚本目录
│   └── demo.py                       # 演示脚本（从 src 移出，移除 sys.path hack）
├── src/multi_agent/                  # 主源码
│   ├── config.py                     # 全局配置（pydantic-settings，从 .env 加载）
│   ├── main.py                       # FastAPI 应用入口
│   ├── prompt_loader.py              # Prompt 加载器（PromptLoader 类封装 + 向后兼容 API）
│   ├── models/                       # 数据模型层
│   │   ├── __init__.py               # 统一导出所有模型
│   │   ├── task.py                   # 任务模型 + 状态机
│   │   ├── project.py                # 项目模型
│   │   ├── message.py                # 消息模型 + Trace 日志模型
│   │   └── prompt.py                 # AgentPrompt 模型（版本化管理）
│   ├── gateway/                      # 网关层
│   │   ├── router.py                 # 意图分类 + 注入检测
│   │   └── auth.py                   # 租户/用户身份提取（TODO: 待集成）
│   ├── agents/                       # Agent 层
│   │   ├── __init__.py               # Worker 注册表（延迟初始化）
│   │   ├── base_worker.py            # Worker 基类（统一执行接口 + Prompt 加载）
│   │   ├── analyzer.py               # 分析 Worker
│   │   ├── coder.py                  # 编码 Worker
│   │   ├── tester.py                 # 测试 Worker
│   │   └── pm_agent.py               # PM Agent（拆解/验收/失败处理）
│   ├── graph/                        # 编排层（拆分自原 workflow.py）
│   │   ├── state.py                  # WorkflowState 状态定义
│   │   ├── nodes.py                  # 所有工作流节点函数
│   │   ├── conditions.py             # 条件路由函数
│   │   └── workflow.py               # 图构建与运行入口（精简）
│   ├── store/                        # 持久化层（拆分自原 pg_store.py）
│   │   ├── pg_store.py               # Facade 门面类，聚合领域子 Store
│   │   ├── project_store.py          # Project CRUD
│   │   ├── task_store.py             # Task CRUD
│   │   ├── trace_store.py            # Trace CRUD
│   │   ├── prompt_store.py           # Prompt CRUD
│   │   └── ddl.py                    # DDL SQL 常量
│   ├── defaults/                     # 默认数据
│   │   └── prompts.py                # 7 个默认 Prompt 模板 + 种子数据
│   └── api/                          # 接口层
│       ├── routes.py                 # REST API 端点（含 Prompt 管理）
│       └── schemas.py                # Pydantic 请求/响应模型
└── tests/                            # 测试目录
    └── test_core.py                  # 核心单元测试
```

## 快速开始

### 环境要求

- Python >= 3.11
- PostgreSQL >= 14（推荐使用 Docker 部署）
- OpenAI API Key（或兼容的 API 端点）

### 安装

```bash
# 1. 克隆项目
git clone <repo-url> && cd multi-agent

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖（含开发依赖）
pip install -e ".[dev]"

# 4. 启动 PostgreSQL（Docker 方式）
docker run -d \
  --name multi-agent-pg \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=multi_agent \
  -p 5432:5432 \
  postgres:16

# 5. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 OPENAI_API_KEY 和 PostgreSQL 密码
```

### 配置项

所有配置通过环境变量或 `.env` 文件设置（`config.py` → `Settings` 类）：

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|---------|--------|------|
| API Key | `OPENAI_API_KEY` | `""` | **必填**，用于调用 LLM |
| API Base URL | `OPENAI_BASE_URL` | `""` | 可选，使用代理或自定义端点时填写 |
| 主力模型 | `OPENAI_API_MODEL` | `gpt-4o` | PM Agent 和 Worker 使用的模型（OpenRouter 格式: `provider/model`） |
| 路由模型 | `GATEWAY_MODEL` | `gpt-4o-mini` | Gateway 路由使用的轻量模型 |
| PG 主机 | `PG_HOST` | `localhost` | PostgreSQL 主机地址 |
| PG 端口 | `PG_PORT` | `5432` | PostgreSQL 端口 |
| PG 用户 | `PG_USER` | `postgres` | PostgreSQL 用户名 |
| PG 密码 | `PG_PASSWORD` | `""` | PostgreSQL 密码 |
| PG 数据库 | `PG_DATABASE` | `multi_agent` | PostgreSQL 数据库名 |
| PG 最小连接 | `PG_MIN_CONNECTIONS` | `2` | asyncpg 连接池最小连接数 |
| PG 最大连接 | `PG_MAX_CONNECTIONS` | `10` | asyncpg 连接池最大连接数 |
| 服务地址 | `HOST` | `0.0.0.0` | HTTP 服务监听地址 |
| 服务端口 | `PORT` | `8000` | HTTP 服务端口 |
| 调试模式 | `DEBUG` | `false` | 启用热重载 |
| 最大重试 | `MAX_RETRIES_PER_TASK` | `3` | 单个任务最大重试次数 |
| 最大任务数 | `MAX_PROJECT_TASKS` | `20` | 单个项目最大子任务数 |
| Token 上限 | `MAX_TOKENS_PER_REQUEST` | `4096` | 单请求最大 Token 数 |
| CORS 来源 | `ALLOWED_ORIGINS` | `*` | 逗号分隔的允许来源，如 `http://localhost:3000,https://example.com` |

### 启动服务

```bash
# 方式一：通过 main.py 启动
python -m multi_agent.main

# 方式二：通过 demo 脚本启动
python scripts/demo.py server

# 方式三：使用 uvicorn（推荐生产环境）
uvicorn multi_agent.main:app --host 0.0.0.0 --port 8000
```

启动后：
- C 端用户页面：`http://localhost:8000/`（对话、项目看板、Trace 日志）
- 管理控制台：`http://localhost:8000/admin`（Agent 管理、Prompt 编辑、模型配置、系统设置）
- 交互式文档：`http://localhost:8000/docs`（Swagger UI）
- 健康检查：`http://localhost:8000/health`

> 系统启动时会自动初始化 PostgreSQL 表结构，并种子填充 7 个默认 Agent Prompt（幂等操作，不会覆盖已有数据）。

### 运行 Demo

```bash
# 即时任务演示
python scripts/demo.py instant

# 项目型任务演示
python scripts/demo.py project

# 运行全部演示
python scripts/demo.py
```

## API 接口

### `POST /api/v1/chat` — 统一入口

Gateway 接收所有请求，自动分类路由到即时任务或项目型任务处理链路。

**请求：**

```json
{
  "message": "分析一下微服务和单体架构的优缺点",
  "tenant_id": "company-a",
  "user_id": "user-001"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 是 | 用户的自然语言请求 |
| `tenant_id` | string | 否 | 租户 ID，默认 `"default"` |
| `user_id` | string | 否 | 用户 ID，默认 `"anonymous"` |

**请求头（可选）：**

| Header | 说明 |
|--------|------|
| `X-Tenant-ID` | 租户标识 |
| `X-User-ID` | 用户标识 |
| `X-Request-ID` | 请求追踪 ID（未提供时自动生成 UUID） |

**响应：**

```json
{
  "response": "任务执行结果的摘要文本",
  "project_id": "PRJ-a1b2c3d4",
  "tasks": [
    {
      "task_id": "PRJ-a1b2c3d4-T001",
      "title": "需求分析",
      "status": "DONE",
      "assigned_worker": "analyzer",
      "output_summary": "...",
      "artifacts": [{"artifact_type": "analysis", "content": "..."}]
    }
  ],
  "trace_count": 4,
  "error": null
}
```

**curl 示例：**

```bash
# 即时任务
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "分析一下 Python 和 Go 在构建 REST API 时的优缺点"}'

# 项目型任务（自动拆解为多个子任务）
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "开发一个用户登录功能：1.先分析需求 2.再写代码 3.最后写测试"}'
```

### `POST /api/v1/chat/stream` — SSE 流式入口

通过 Server-Sent Events (SSE) 实时推送任务执行进度，前端可展示实时进度时间线。

**事件类型：** `gateway`（路由决策）、`task_start`（任务开始）、`task_done`（任务完成）、`pm_review`（PM 验收）、`error`（错误）、`done`（全部完成）。

```bash
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我写一个 Python 快速排序函数"}'
```

### `POST /api/v1/test-llm` — 测试 LLM 连接

管理端使用，测试当前配置的 LLM 模型是否可用。

```bash
curl -X POST http://localhost:8000/api/v1/test-llm
```

### `GET /api/v1/projects/{project_id}` — 查询项目

获取项目详情和所有子任务状态。

```bash
curl http://localhost:8000/api/v1/projects/PRJ-a1b2c3d4
```

### `GET /api/v1/tasks/{task_id}` — 查询任务

获取单个任务的详细信息。

```bash
curl http://localhost:8000/api/v1/tasks/PRJ-abc-T001
```

### `PATCH /api/v1/tasks/{task_id}` — 人工介入

当任务处于 `HUMAN_PENDING` 状态时，人工操作员可以通过此接口恢复任务。

```bash
curl -X PATCH http://localhost:8000/api/v1/tasks/PRJ-abc-T002 \
  -H "Content-Type: application/json" \
  -d '{
    "status": "TODO",
    "resolution": "human_fix",
    "comment": "已修复外部依赖问题，可以重试"
  }'
```

| 字段 | 可选值 | 说明 |
|------|--------|------|
| `status` | `"TODO"` 或 `"DONE"` | TODO = 重试执行，DONE = 人工直接标记完成 |
| `resolution` | 任意字符串 | 解决方式说明 |
| `comment` | 任意字符串 | 人工备注 |

### `GET /api/v1/projects/{project_id}/trace` — 调用链路

获取项目中所有 Agent 调用的 Trace 日志，用于调试和审计。

```bash
curl http://localhost:8000/api/v1/projects/PRJ-a1b2c3d4/trace
```

响应中每条 Trace 包含：`agent_name`、`latency_ms`、`prompt_tokens`、`completion_tokens`、`failure_reason` 等字段。

### `GET /api/v1/prompts` — 查询 Prompt 列表

获取所有 Agent Prompt，可按 `agent_name` 过滤。

```bash
# 列出所有 Prompt
curl http://localhost:8000/api/v1/prompts

# 按 Agent 过滤
curl http://localhost:8000/api/v1/prompts?agent_name=pm
```

### `GET /api/v1/prompts/{prompt_id}` — 查询单个 Prompt

获取指定 `prompt_id` 的当前生效版本。

```bash
curl http://localhost:8000/api/v1/prompts/pm_decompose
```

### `PUT /api/v1/prompts/{prompt_id}` — 更新 Prompt

更新 Prompt 内容，系统自动创建新版本（旧版本标记为非活跃）。

```bash
curl -X PUT http://localhost:8000/api/v1/prompts/pm_decompose \
  -H "Content-Type: application/json" \
  -d '{
    "content": "更新后的 Prompt 内容...",
    "description": "更新了任务拆解规则"
  }'
```

## 核心机制

### 请求处理完整流程

```
1. HTTP 请求到达 FastAPI
2. 中间件注入 request_id（UUID）
3. /api/v1/chat 端点接收请求
4. 调用 run_workflow()，初始化 WorkflowState
5. LangGraph 执行状态图：

   [gateway_route]
     ├── 检查 Prompt 注入 → 检测到则 blocked
     └── 调用轻量模型做意图分类
           ├── instant → instant_handler → END
           ├── project → project_init → ...
           ├── blocked → blocked_handler → END
           └── scheduled → scheduled_handler → END（MVP 暂不支持）

   [project_init]（仅项目型任务）
     ├── 创建 Project 记录并写入 PostgreSQL
     └── PM Agent 拆解为 N 个子任务

   [worker_execute]（循环执行每个任务）
     ├── TODO → DOING → 调用 Worker → REVIEW / FAILED
     ├── 收集前序任务产物作为上下文传递
     └── Trace 日志持久化到数据库

   [pm_review]
     ├── 通过: REVIEW → DONE → 下一个任务
     └── 不通过: REVIEW → TODO → 重试（retry_count 由 handle_failure 统一管理）

   [handle_failure]
     ├── 状态前置检查，避免非法转换导致 ValueError
     ├── retry → 重新执行
     ├── escalate → HUMAN_PENDING
     ├── abort → FAILED
     └── Trace 日志持久化到数据库

   [project_finalize]
     └── 汇总结果（含 HUMAN_PENDING 任务时项目状态为 PAUSED）→ END
```

### 任务状态机

```
                    ┌─────────────────────────────────┐
                    │                                 │
                    ▼                                 │
TODO ──────► DOING ──────► REVIEW ──────► DONE       │
 │            │               │                      │
 │            │               │                      │
 │            ▼               ▼                      │
 │         BLOCKED      TODO(打回重试)                │
 │            │                                      │
 │            ▼                                      │
 └────► FAILED ──────► HUMAN_PENDING ──────► DONE    │
              │               │                      │
              └───────────────┘                      │
                                                     │
              HUMAN_PENDING ──────► TODO ────────────┘
```

| 状态 | 含义 | 可转换到 |
|------|------|---------|
| `TODO` | 等待执行 | DOING, FAILED |
| `DOING` | 正在执行 | REVIEW, BLOCKED, FAILED, TODO |
| `BLOCKED` | 等待外部输入 | TODO, FAILED, HUMAN_PENDING |
| `REVIEW` | 等待 PM 验收 | DONE, TODO(打回), FAILED |
| `DONE` | 已完成 | （终态） |
| `FAILED` | 超过重试上限或致命错误 | TODO, HUMAN_PENDING |
| `HUMAN_PENDING` | 等待人工介入 | TODO, DONE |

### Prompt 数据库化管理

所有 Agent 的系统 Prompt 不再硬编码在代码中，而是存储在 PostgreSQL `agent_prompts` 表中：

- **运行时加载**：Agent 执行时通过 `PromptLoader` 类（依赖注入 `PgStore`）从数据库加载最新 Prompt，加载失败时 fallback 到 `defaults/prompts.py` 中的硬编码默认值；同时保留向后兼容的模块级函数 API
- **版本管理**：每次更新自动创建新版本（`version` 自增），旧版本标记为非活跃（`is_active = false`），保留完整历史
- **种子填充**：系统启动时自动插入 7 个默认 Prompt（幂等操作），包括 `pm_decompose`、`pm_review`、`pm_failure`、`analyzer`、`coder`、`tester`、`gateway_routing`
- **在线编辑**：通过管理控制台（`/admin`）或 API（`PUT /api/v1/prompts/{prompt_id}`）实时修改 Prompt，无需重启服务

### Worker 产物传递机制

Worker 之间**不直接通信**，所有产物通过 PostgreSQL 中转：

```
Worker A 完成分析
  └── 产物写入 PostgreSQL（artifacts JSONB 字段）

PM 验收 Worker A 的产物
  ├── 对照 acceptance_criteria 逐条检查
  └── 通过 → DONE / 不通过 → 打回

Worker B 开始编码
  └── 从 state 中读取 Worker A 的产物作为上下文
  └── 不依赖 Worker A 是否还在运行
```

直接通信会形成隐式依赖，任何一方超时或失败都会级联阻塞，且无法单独重试某个节点。

### Worker 类型

| Worker | 职责 | 产物类型 |
|--------|------|---------|
| **Analyzer** | 需求分析、文档研究、问题诊断 | `analysis` |
| **Coder** | 代码生成、功能实现、脚本编写 | `code` |
| **Tester** | 测试计划、用例编写、代码审查 | `test_report` |

所有 Worker 统一输出结构化 JSON（`WorkerOutput`），包含 `status`、`summary`、`artifacts`、`error` 字段。

### 模型分层策略

系统通过 [OpenRouter](https://openrouter.ai/) 接入多个开源模型，按职责分层：

| 角色 | 当前模型 | 选择理由 |
|------|---------|--------|
| **Gateway 路由** | `meta-llama/llama-3.1-8b-instruct` | 只需稳定输出分类 JSON，追求低延迟低成本（~1.7s） |
| **PM Agent** | `mistralai/mistral-small-3.2-24b-instruct` | 需要任务拆解能力和指令遵循（~2.1s） |
| **Worker Agent** | `mistralai/mistral-small-3.2-24b-instruct` | 需要专业领域能力（代码/分析/测试） |

> 模型通过 `.env` 配置：`OPENAI_API_MODEL`（PM/Worker 共用）和 `GATEWAY_MODEL`（路由专用）。
> OpenRouter 接入时注意：`openai/gpt-4o`、`anthropic/claude` 在某些地区不可用（403）。

## 开发

```bash
# 运行测试
python -m pytest tests/ -v

# 代码检查
ruff check src/

# 代码格式化
ruff format src/
```

### 添加新 Worker

1. 在 `agents/` 下创建新文件，继承 `BaseWorker`
2. 实现 `_parse_output()` 方法
3. 在 `agents/__init__.py` 的 `_WORKER_CLASSES` 字典中注册
4. 在 `defaults/prompts.py` 中添加对应的默认 Prompt
5. 重启服务后，新 Prompt 会自动种子填充到数据库

## 技术栈

| 模块 | 技术 |
|------|------|
| 工作流引擎 | LangGraph |
| LLM 集成 | LangChain + OpenAI |
| Web 框架 | FastAPI + Uvicorn |
| 数据验证 | Pydantic v2 |
| 配置管理 | pydantic-settings |
| 数据库 | PostgreSQL（asyncpg 异步连接池） |
| 构建工具 | Hatchling |
| 测试 | pytest + pytest-asyncio |
| 代码质量 | ruff |

## 典型使用场景

### 场景一：即时任务

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "分析一下 Python 和 Go 在构建 REST API 时的优缺点"}'
```

Gateway 分类为 `instant` → 路由到 Analyzer → 直接返回结果。

### 场景二：项目型任务

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "开发一个 Todo 应用 REST API：1.分析需求 2.用 FastAPI 实现 3.编写测试计划"}'
```

Gateway 分类为 `project` → PM 拆解为 3 个子任务 → 依次执行 Analyzer → Coder → Tester → 汇总返回。

### 场景三：失败打回与重试

Coder 输出不符合验收标准 → PM 打回 → Worker 重新执行（附带打回原因）→ PM 再次验收 → 通过。

### 场景四：人工介入

任务多次重试仍失败 → PM 升级为 `HUMAN_PENDING` → 运维人员通过 `PATCH /tasks/{task_id}` 恢复 → 任务重新进入执行队列。

## 待完成 / TODO

对照《多Agent设计方案》落地路线，以下为当前系统尚未实现的功能模块：

### Beta 阶段

| 功能 | 说明 |
|------|------|
| **定时任务链路** | APScheduler + PG JobStore + Redis 分布式锁，支持自然语言转结构化调度请求 |
| **RAG 独立检索服务** | Qdrant 向量库 + 混合检索（Dense + BM25）+ Reranker，服务端强制注入 `tenant_id` 过滤 |
| **Langfuse Trace 集成** | 替换本地日志，接入开源 Langfuse 实现 Agent 调用链可视化追踪 |
| **Celery + Redis 异步队列** | 处理耗时任务，支持并发消费和失败重试 |
| **多租户完整隔离** | 当前仅有 `tenant_id` 字段过滤，缺少中间件级强制隔离和 RAG 服务端注入 |
| **人工介入通知渠道** | 任务进入 `HUMAN_PENDING` 时通过 IM / 邮件 / 看板主动通知负责人 |

### Production 阶段

| 功能 | 说明 |
|------|------|
| **完整权限模型** | RBAC + 工具白名单 + API 鉴权，不同 Agent 绑定不同工具权限 |
| **评测机制** | 黄金集标注 + 回归测试 + 自动评测（LLM-as-Judge），发版前自动运行 |
| **Prompt 注入模型分类层** | 当前仅有规则匹配，需补充小模型分类层作为第二道防线 |
| **灰度发布与回滚** | 支持编排流程的灰度切换和版本回滚 |
| **成本控制与模型降级** | 单请求预算、单项目最大轮次、超出预算自动降级到更小模型 |
| **幂等键机制** | 防止请求重复提交导致任务重复执行 |
| **死信机制与补偿逻辑** | 不可恢复任务的死信队列和自动补偿 |

## 常见问题

**没有 OpenAI API Key 能用吗？**
可以通过设置 `OPENAI_BASE_URL` 指向任何兼容 OpenAI API 的端点，如 OpenRouter（`https://openrouter.ai/api/v1`）、Ollama、Azure OpenAI 等。使用 OpenRouter 时可接入 mistral、llama、deepseek 等开源模型。

**如何修改最大重试次数？**
在 `.env` 中设置 `MAX_RETRIES_PER_TASK=5`，或在 PM 拆解任务时为每个任务单独指定。

**项目执行到一半失败了怎么办？**
已完成的任务（DONE 状态）不会丢失，持久化在 PostgreSQL 中。失败的任务会根据 PM 决策进行重试或升级为人工介入。

**如何查看 Token 消耗？**
通过 `GET /api/v1/projects/{id}/trace` 接口，每条 Trace 包含 `prompt_tokens` 和 `completion_tokens`。

**如何修改 Agent 的 Prompt？**
通过管理控制台（`http://localhost:8000/admin`）在线编辑，或通过 `PUT /api/v1/prompts/{prompt_id}` API 更新。修改即时生效，无需重启服务。

## License

MIT

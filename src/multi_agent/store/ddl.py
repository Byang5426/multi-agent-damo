"""PostgreSQL DDL 建表语句常量。"""

INIT_SQL = """
-- 项目表
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PLANNING',
    tenant_id    TEXT NOT NULL DEFAULT 'default',
    created_by   TEXT NOT NULL DEFAULT 'system',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projects_tenant
    ON projects (tenant_id);
CREATE INDEX IF NOT EXISTS idx_projects_status
    ON projects (status);

-- 任务表
CREATE TABLE IF NOT EXISTS tasks (
    task_id              TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    title                TEXT NOT NULL,
    description          TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'TODO',
    assigned_worker      TEXT,
    retry_count          INTEGER NOT NULL DEFAULT 0,
    max_retries          INTEGER NOT NULL DEFAULT 3,
    last_error           TEXT,
    output_summary       TEXT,
    acceptance_criteria  JSONB NOT NULL DEFAULT '[]'::jsonb,
    artifacts            JSONB NOT NULL DEFAULT '[]'::jsonb,
    tenant_id            TEXT NOT NULL DEFAULT 'default',
    created_by           TEXT NOT NULL DEFAULT 'system',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by           TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_tasks_project
    ON tasks (project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant
    ON tasks (tenant_id);
-- 部分索引：只索引需要人工处理的任务
CREATE INDEX IF NOT EXISTS idx_tasks_human_pending
    ON tasks (project_id) WHERE status = 'HUMAN_PENDING';
-- GIN 索引：加速 artifacts JSONB 查询
CREATE INDEX IF NOT EXISTS idx_tasks_artifacts_gin
    ON tasks USING GIN (artifacts);

-- Trace 日志表
CREATE TABLE IF NOT EXISTS trace_logs (
    span_id            TEXT PRIMARY KEY,
    trace_id           TEXT NOT NULL,
    parent_span_id     TEXT,
    request_id         TEXT,
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    task_id            TEXT,
    agent_name         TEXT NOT NULL,
    tool_calls         JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms         INTEGER,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    failure_reason     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trace_task
    ON trace_logs (task_id);
CREATE INDEX IF NOT EXISTS idx_trace_trace_id
    ON trace_logs (trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_tenant
    ON trace_logs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_trace_created
    ON trace_logs (created_at DESC);
-- GIN 索引：加速 tool_calls JSONB 查询
CREATE INDEX IF NOT EXISTS idx_trace_tool_calls_gin
    ON trace_logs USING GIN (tool_calls);

-- Agent Prompt 管理表
CREATE TABLE IF NOT EXISTS agent_prompts (
    prompt_id    TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'system',
    version      INTEGER NOT NULL DEFAULT 1,
    content      TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    is_active    BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (prompt_id, version)
);

CREATE INDEX IF NOT EXISTS idx_prompts_agent
    ON agent_prompts (agent_name);
CREATE INDEX IF NOT EXISTS idx_prompts_active
    ON agent_prompts (prompt_id) WHERE is_active = true;

-- 定时调度任务表
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL,
    cron_expression  TEXT NOT NULL,
    timezone         TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    status           TEXT NOT NULL DEFAULT 'ACTIVE',
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    created_by       TEXT NOT NULL DEFAULT 'system',
    last_run_at      TIMESTAMPTZ,
    next_run_at      TIMESTAMPTZ,
    run_count        INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schedules_status
    ON schedules (status);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run
    ON schedules (next_run_at) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_schedules_tenant
    ON schedules (tenant_id);
"""

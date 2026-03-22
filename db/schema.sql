-- BossMod AI — DuckDB schema
-- Execute with IF NOT EXISTS so it is safe to run on every startup.
-- DuckDB-compatible DDL: VARCHAR, TEXT, INTEGER, FLOAT, DECIMAL, BOOLEAN,
-- TIMESTAMP, gen_random_uuid(), CHECK constraints.

-- ───────────────────────────────────────────────────────────────────────────
-- Agents — persistent identity & configuration
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agents (
    id                            VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    name                          VARCHAR NOT NULL,
    role                          VARCHAR,
    prompt_template               TEXT,
    color                         VARCHAR DEFAULT '#3b82f6',
    model_social                  VARCHAR,
    model_work                    VARCHAR,
    model_reasoning               VARCHAR,
    model_extraction              VARCHAR,
    model_self_queue              VARCHAR,
    api_base_url                  VARCHAR,
    api_key                       VARCHAR,
    extra_body                    TEXT,
    desk_x                        INTEGER,
    desk_y                        INTEGER,
    guardian_token_limit           INTEGER DEFAULT 30000,
    guardian_velocity_limit        INTEGER DEFAULT 10,
    guardian_repetition_threshold  FLOAT   DEFAULT 0.85,
    guardian_no_progress_threshold INTEGER DEFAULT 30,
    created_at                    TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Agent state — runtime position & activity (one row per agent)
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id        VARCHAR PRIMARY KEY REFERENCES agents(id),
    x               INTEGER DEFAULT 0,
    y               INTEGER DEFAULT 0,
    status          VARCHAR DEFAULT 'idle'
                        CHECK (status IN ('idle', 'work_active', 'social_active', 'in_transit')),
    last_active_at  TIMESTAMP,
    idle_since      TIMESTAMP DEFAULT current_timestamp,
    current_task_id VARCHAR
);

-- ───────────────────────────────────────────────────────────────────────────
-- Messages — inter-agent and system communication
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS messages (
    id            VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    from_agent    VARCHAR NOT NULL,
    to_agent      VARCHAR,
    content       TEXT    NOT NULL,
    message_type  VARCHAR DEFAULT 'work'
                      CHECK (message_type IN ('work', 'social', 'human', 'system', 'meeting')),
    location_x    INTEGER,
    location_y    INTEGER,
    token_count   INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Tasks — units of work assigned to agents
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tasks (
    id             VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    title          VARCHAR NOT NULL,
    description    TEXT,
    project        VARCHAR,
    assigned_to    VARCHAR,
    created_by     VARCHAR,
    status         VARCHAR DEFAULT 'pending'
                       CHECK (status IN ('pending', 'active', 'blocked', 'complete',
                                         'stalled', 'abandoned', 'delegated')),
    parent_task_id VARCHAR,
    cost_ceiling   DECIMAL,
    completion_summary TEXT,
    status_note    TEXT,
    watchdog_pinged_at TIMESTAMP,
    last_activity  TIMESTAMP DEFAULT current_timestamp,
    created_at     TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Memory nodes — entity-attribute-value knowledge graph
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS memory_nodes (
    id          VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    VARCHAR NOT NULL REFERENCES agents(id),
    entity      VARCHAR NOT NULL,
    attribute   VARCHAR NOT NULL,
    value       TEXT,
    confidence  FLOAT   DEFAULT 1.0,
    source      VARCHAR DEFAULT 'work'
                    CHECK (source IN ('work', 'meeting', 'message', 'social', 'transit')),
    created_at  TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- CLI log — command audit trail with risk classification
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cli_log (
    id         VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id   VARCHAR NOT NULL REFERENCES agents(id),
    command    TEXT    NOT NULL,
    risk_tier  VARCHAR DEFAULT 'ambiguous'
                   CHECK (risk_tier IN ('safe', 'slow', 'review', 'blocked', 'ambiguous')),
    outcome    VARCHAR DEFAULT 'pending'
                   CHECK (outcome IN ('executed', 'approved', 'blocked', 'pending')),
    approver   VARCHAR,
    created_at TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Approvals — human-in-the-loop action gating
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS approvals (
    id              VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        VARCHAR NOT NULL REFERENCES agents(id),
    task_id         VARCHAR,
    action_type     VARCHAR NOT NULL,
    action_detail   TEXT,
    agent_reasoning TEXT,
    status          VARCHAR DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'denied')),
    decided_by      VARCHAR,
    created_at      TIMESTAMP DEFAULT current_timestamp,
    decided_at      TIMESTAMP
);

-- ───────────────────────────────────────────────────────────────────────────
-- AI Connections — saved LLM provider configurations
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ai_connections (
    id           VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR NOT NULL,
    api_base_url VARCHAR NOT NULL,
    api_key      VARCHAR,
    model        VARCHAR,
    extra_body   TEXT,
    created_at   TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- AI Personalities — reusable prompt templates for agent roles
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ai_personalities (
    id              VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR NOT NULL,
    prompt_template TEXT    NOT NULL,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Activity log — persistent event history for the UI activity feed
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS activity_log (
    id          VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    event       VARCHAR NOT NULL,
    detail      TEXT    NOT NULL,
    agent_name  VARCHAR,
    created_at  TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Settings — key-value application configuration
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS settings (
    key        VARCHAR PRIMARY KEY,
    value      TEXT    NOT NULL,
    category   VARCHAR DEFAULT 'general',
    updated_at TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Projects — named containers for tasks
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS projects (
    id          VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Agent ↔ Project many-to-many
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_projects (
    agent_id    VARCHAR REFERENCES agents(id),
    project_id  VARCHAR REFERENCES projects(id),
    assigned_at TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (agent_id, project_id)
);

-- ───────────────────────────────────────────────────────────────────────────
-- Schedules — cron-driven recurring actions
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schedules (
    id              VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        VARCHAR NOT NULL REFERENCES agents(id),
    cron_expression VARCHAR NOT NULL,
    description     TEXT,
    enabled         BOOLEAN DEFAULT true,
    last_run_at     TIMESTAMP,
    next_run_at     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Agent triggers — durable wake-up queue
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_triggers (
    id             VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id       VARCHAR NOT NULL REFERENCES agents(id),
    trigger_type   VARCHAR NOT NULL,
    source_channel VARCHAR NOT NULL
                      CHECK (source_channel IN ('chat', 'work', 'system')),
    payload        TEXT NOT NULL,
    task_id        VARCHAR,
    status         VARCHAR NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'claimed', 'completed', 'failed')),
    failure_reason TEXT,
    claimed_at     TIMESTAMP,
    completed_at   TIMESTAMP,
    failed_at      TIMESTAMP,
    created_at     TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Rooms — spatial regions on the office tilemap
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS rooms (
    id              VARCHAR PRIMARY KEY,
    name            VARCHAR NOT NULL,
    display_name    VARCHAR,
    room_type       VARCHAR DEFAULT 'hallway'
                        CHECK (room_type IN ('workspace', 'meeting', 'break', 'transit', 'hallway')),
    bounds_x1       INTEGER,
    bounds_y1       INTEGER,
    bounds_x2       INTEGER,
    bounds_y2       INTEGER,
    allowed_actions TEXT
);

-- ───────────────────────────────────────────────────────────────────────────
-- Diagnostics — one row per agent turn, full trace data
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS diagnostics (
    id                  VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id            VARCHAR NOT NULL,
    agent_name          VARCHAR NOT NULL,
    trigger_type        VARCHAR NOT NULL,
    trigger_data        TEXT NOT NULL,
    status              VARCHAR NOT NULL DEFAULT 'success'
                            CHECK (status IN ('success', 'error', 'skipped')),
    mode                VARCHAR,
    model               VARCHAR,
    model_source        VARCHAR,
    context             TEXT,
    raw_response        TEXT,
    action_name         VARCHAR,
    parsed_action       TEXT,
    result              TEXT,
    prompt_tokens       INTEGER DEFAULT 0,
    completion_tokens   INTEGER DEFAULT 0,
    total_tokens        INTEGER DEFAULT 0,
    error               TEXT,
    duration_ms         INTEGER DEFAULT 0,
    created_at          TIMESTAMP DEFAULT current_timestamp
);

-- BossMod AI — schema
-- Execute with IF NOT EXISTS so it is safe to run on every startup.

-- ───────────────────────────────────────────────────────────────────────────
-- Agents — persistent identity & configuration
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agents (
    id                            VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
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
                        CHECK (status IN ('idle', 'waiting', 'blocked', 'work_active', 'social_active', 'in_transit')),
    last_active_at  TIMESTAMP,
    idle_since      TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Agent CLI state — persistent virtual CLI working directory
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_cli_state (
    agent_id   VARCHAR PRIMARY KEY REFERENCES agents(id),
    cwd        VARCHAR NOT NULL DEFAULT '/me',
    updated_at TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS agent_prompt_history_policies (
    agent_id                    VARCHAR PRIMARY KEY REFERENCES agents(id),
    last_n_histories            INTEGER NOT NULL DEFAULT 30 CHECK (last_n_histories >= 0),
    max_allowed_history_tokens  INTEGER NOT NULL DEFAULT 2000 CHECK (max_allowed_history_tokens >= 0),
    earliest_ts_allowed         TIMESTAMP,
    include_notifications       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMP DEFAULT current_timestamp,
    updated_at                  TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS agent_storage_identities (
    agent_id       VARCHAR PRIMARY KEY REFERENCES agents(id),
    storage_index  INTEGER NOT NULL UNIQUE,
    storage_key    VARCHAR NOT NULL UNIQUE,
    created_at     TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Messages — inter-agent and system communication
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS messages (
    id            VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    from_agent    VARCHAR NOT NULL,
    to_agent      VARCHAR,
    content       TEXT    NOT NULL,
    message_type  VARCHAR DEFAULT 'work'
                      CHECK (message_type IN ('work', 'social', 'human', 'meeting')),
    location_x    INTEGER,
    location_y    INTEGER,
    token_count   INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS meeting_sessions (
    id                  VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    room_id             VARCHAR NOT NULL,
    title               VARCHAR NOT NULL,
    status              VARCHAR NOT NULL
                            CHECK (status IN ('active', 'ended')),
    created_by_agent_id VARCHAR REFERENCES agents(id),
    created_at          TIMESTAMP DEFAULT current_timestamp,
    updated_at          TIMESTAMP DEFAULT current_timestamp,
    ended_at            TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meeting_session_messages (
    id              VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    session_id      VARCHAR NOT NULL REFERENCES meeting_sessions(id),
    author_type     VARCHAR NOT NULL
                        CHECK (author_type IN ('human', 'agent', 'system')),
    author_agent_id VARCHAR REFERENCES agents(id),
    author_name     VARCHAR NOT NULL,
    content         TEXT NOT NULL,
    source_channel  VARCHAR NOT NULL,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS meeting_response_rounds (
    id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    session_id        VARCHAR NOT NULL REFERENCES meeting_sessions(id),
    source_message_id VARCHAR NOT NULL REFERENCES meeting_session_messages(id),
    status            VARCHAR NOT NULL
                         CHECK (status IN ('active', 'completed')),
    created_at        TIMESTAMP DEFAULT current_timestamp,
    updated_at        TIMESTAMP DEFAULT current_timestamp,
    completed_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meeting_response_candidates (
    id             VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    round_id       VARCHAR NOT NULL REFERENCES meeting_response_rounds(id),
    agent_id       VARCHAR NOT NULL REFERENCES agents(id),
    status         VARCHAR NOT NULL
                      CHECK (status IN ('pending', 'queued', 'responding', 'responded', 'observed')),
    queue_position INTEGER,
    created_at     TIMESTAMP DEFAULT current_timestamp,
    updated_at     TIMESTAMP DEFAULT current_timestamp,
    completed_at   TIMESTAMP,
    UNIQUE(round_id, agent_id)
);

CREATE TABLE IF NOT EXISTS channels (
    id          VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    name        VARCHAR NOT NULL,
    kind        VARCHAR NOT NULL
                    CHECK (kind IN ('manual')),
    status      VARCHAR NOT NULL
                    CHECK (status IN ('active', 'archived')),
    created_by  VARCHAR,
    created_at  TIMESTAMP DEFAULT current_timestamp,
    updated_at  TIMESTAMP DEFAULT current_timestamp,
    archived_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channel_members (
    channel_id   VARCHAR NOT NULL REFERENCES channels(id),
    agent_id     VARCHAR NOT NULL REFERENCES agents(id),
    created_at   TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (channel_id, agent_id)
);

CREATE TABLE IF NOT EXISTS channel_messages (
    id              VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    channel_id       VARCHAR NOT NULL REFERENCES channels(id),
    author_type      VARCHAR NOT NULL
                         CHECK (author_type IN ('human', 'agent', 'system')),
    author_agent_id  VARCHAR REFERENCES agents(id),
    author_name      VARCHAR NOT NULL,
    content          TEXT NOT NULL,
    source_channel   VARCHAR NOT NULL,
    created_at       TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS channel_response_rounds (
    id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    channel_id        VARCHAR NOT NULL REFERENCES channels(id),
    source_message_id VARCHAR NOT NULL REFERENCES channel_messages(id),
    status            VARCHAR NOT NULL
                         CHECK (status IN ('active', 'completed')),
    created_at        TIMESTAMP DEFAULT current_timestamp,
    updated_at        TIMESTAMP DEFAULT current_timestamp,
    completed_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channel_response_candidates (
    id             VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    round_id       VARCHAR NOT NULL REFERENCES channel_response_rounds(id),
    agent_id       VARCHAR NOT NULL REFERENCES agents(id),
    status         VARCHAR NOT NULL
                      CHECK (status IN ('pending', 'queued', 'responding', 'responded', 'observed')),
    queue_position INTEGER,
    created_at     TIMESTAMP DEFAULT current_timestamp,
    updated_at     TIMESTAMP DEFAULT current_timestamp,
    completed_at   TIMESTAMP,
    UNIQUE(round_id, agent_id)
);

-- ───────────────────────────────────────────────────────────────────────────
-- Tasks — units of work assigned to agents
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tasks (
    id             VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    title          VARCHAR NOT NULL,
    description    TEXT,
    project        VARCHAR,
    assigned_to    VARCHAR,
    requester_id   VARCHAR,
    owner_id       VARCHAR,
    created_by     VARCHAR,
    status         VARCHAR DEFAULT 'pending'
                       CHECK (status IN ('pending', 'accepted', 'active', 'waiting', 'blocked', 'complete',
                                         'stalled', 'abandoned', 'delegated', 'declined')),
    parent_task_id VARCHAR,
    cost_ceiling   DECIMAL,
    completion_summary TEXT,
    status_note    TEXT,
    watchdog_pinged_at TIMESTAMP,
    last_progress_at TIMESTAMP DEFAULT current_timestamp,
    last_heartbeat_at TIMESTAMP DEFAULT current_timestamp,
    last_activity  TIMESTAMP DEFAULT current_timestamp,
    created_at     TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS task_work_contracts (
    task_id        VARCHAR PRIMARY KEY REFERENCES tasks(id),
    work_contract  TEXT    NOT NULL,
    created_at     TIMESTAMP DEFAULT current_timestamp,
    updated_at     TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS task_notification_policies (
    task_id         VARCHAR PRIMARY KEY REFERENCES tasks(id),
    source_channel  VARCHAR NOT NULL,
    policy          VARCHAR NOT NULL
                       CHECK (policy IN ('none', 'completion_blocked', 'all')),
    created_at      TIMESTAMP DEFAULT current_timestamp,
    updated_at      TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS task_notification_targets (
    task_id      VARCHAR PRIMARY KEY REFERENCES tasks(id),
    channel_id   VARCHAR REFERENCES channels(id),
    created_at   TIMESTAMP DEFAULT current_timestamp,
    updated_at   TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS task_events (
    id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    task_id           VARCHAR NOT NULL REFERENCES tasks(id),
    author_type       VARCHAR NOT NULL
                          CHECK (author_type IN ('human', 'agent', 'system')),
    author_agent_id   VARCHAR REFERENCES agents(id),
    author_name       VARCHAR NOT NULL,
    event_type        VARCHAR NOT NULL
                          CHECK (event_type IN (
                              'comment',
                              'clarification',
                              'answer',
                              'status_update',
                              'blocker',
                              'completion',
                              'assignment',
                              'reprioritized',
                              'system'
                          )),
    content           TEXT NOT NULL,
    source_message_id VARCHAR,
    source_trigger_id VARCHAR,
    created_at        TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- AI Connections — saved LLM provider configurations
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ai_connections (
    id           VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
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
    id              VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    name            VARCHAR NOT NULL,
    prompt_template TEXT    NOT NULL,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Activity log — persistent event history for the UI activity feed
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS activity_log (
    id          VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    event       VARCHAR NOT NULL,
    detail      TEXT    NOT NULL,
    agent_name  VARCHAR,
    created_at  TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- CLI policy rules — configurable command whitelist/blacklist/elevated
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cli_policy_rules (
    id          VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    tier        VARCHAR NOT NULL
                    CHECK (tier IN ('never_allowed', 'always_allowed', 'approval_required')),
    pattern     VARCHAR NOT NULL,
    match_mode  VARCHAR NOT NULL DEFAULT 'prefix'
                    CHECK (match_mode IN ('exact', 'prefix', 'glob')),
    agent_id    VARCHAR REFERENCES agents(id),
    description VARCHAR,
    category    VARCHAR NOT NULL DEFAULT 'general',
    usage_syntax VARCHAR,
    help_text   TEXT,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    priority    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP DEFAULT current_timestamp,
    updated_at  TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- CLI approval requests — human-in-the-loop command gating
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cli_approval_requests (
    id              VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    agent_id        VARCHAR NOT NULL REFERENCES agents(id),
    trigger_id      VARCHAR,
    command         TEXT NOT NULL,
    content         TEXT,
    cwd             VARCHAR,
    matched_rule_id VARCHAR REFERENCES cli_policy_rules(id),
    status          VARCHAR NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    decision_by     VARCHAR,
    decision_note   TEXT,
    decided_at      TIMESTAMP,
    expires_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- BossMod CLI events — per-command audit log
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bm_cli_events (
    id                  VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    agent_id            VARCHAR NOT NULL REFERENCES agents(id),
    command             TEXT NOT NULL,
    content_present     BOOLEAN DEFAULT FALSE,
    executor            VARCHAR NOT NULL,
    cwd_before          VARCHAR,
    cwd_after           VARCHAR,
    policy_tier         VARCHAR NOT NULL,
    decision            VARCHAR NOT NULL
                            CHECK (decision IN ('allowed', 'approval_required', 'denied')),
    exit_code           INTEGER DEFAULT 0,
    result_kind         VARCHAR,
    stdout_preview      TEXT,
    stderr_preview      TEXT,
    changed_paths       TEXT,
    trigger_type        VARCHAR,
    approval_request_id VARCHAR REFERENCES cli_approval_requests(id),
    created_at          TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS artifacts (
    id             VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    agent_id       VARCHAR NOT NULL REFERENCES agents(id),
    task_id        VARCHAR REFERENCES tasks(id),
    virtual_path   VARCHAR NOT NULL,
    absolute_path  VARCHAR NOT NULL UNIQUE,
    title          VARCHAR NOT NULL,
    kind           VARCHAR NOT NULL
                       CHECK (kind IN ('file')),
    category       VARCHAR NOT NULL
                       CHECK (category IN ('output', 'note', 'project')),
    size_bytes     BIGINT DEFAULT 0,
    source_command TEXT,
    created_at     TIMESTAMP DEFAULT current_timestamp,
    updated_at     TIMESTAMP DEFAULT current_timestamp
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
-- Agent triggers — durable wake-up queue
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_triggers (
    id             VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    agent_id       VARCHAR NOT NULL REFERENCES agents(id),
    trigger_type   VARCHAR NOT NULL,
    source_channel VARCHAR NOT NULL
                      CHECK (source_channel IN ('chat', 'channel', 'work', 'system')),
    payload        TEXT NOT NULL,
    task_id        VARCHAR,
    status         VARCHAR NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'claimed', 'completed', 'failed')),
    retry_count    INTEGER NOT NULL DEFAULT 0,
    failure_reason TEXT,
    claimed_at     TIMESTAMP,
    completed_at   TIMESTAMP,
    failed_at      TIMESTAMP,
    created_at     TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS runtime_commands (
    id             VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    command_type   VARCHAR NOT NULL
                      CHECK (command_type IN (
                          'wake_dispatcher',
                          'pause_runtime',
                          'resume_runtime',
                          'reset_agent_runtime',
                          'shutdown_runtime'
                      )),
    payload        TEXT NOT NULL,
    status         VARCHAR NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'claimed', 'completed', 'failed')),
    failure_reason TEXT,
    claimed_at     TIMESTAMP,
    completed_at   TIMESTAMP,
    failed_at      TIMESTAMP,
    created_at     TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS runtime_worker_state (
    worker_name       VARCHAR PRIMARY KEY,
    pid               INTEGER,
    lifecycle_state   VARCHAR NOT NULL
                         CHECK (lifecycle_state IN ('starting', 'running', 'stopping', 'stopped', 'error')),
    last_heartbeat_at TIMESTAMP,
    started_at        TIMESTAMP,
    stopped_at        TIMESTAMP,
    last_error        TEXT,
    updated_at        TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Activities — durable runtime activity state
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS activities (
    id                 VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    agent_id           VARCHAR NOT NULL REFERENCES agents(id),
    kind               VARCHAR NOT NULL
                           CHECK (kind IN ('assignment', 'break', 'conversation', 'meeting', 'movement', 'social', 'work')),
    status             VARCHAR NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'paused', 'completed', 'cancelled')),
    task_id            VARCHAR,
    parent_activity_id VARCHAR REFERENCES activities(id),
    title              VARCHAR,
    detail             TEXT,
    destination        VARCHAR,
    metadata           TEXT,
    created_at         TIMESTAMP DEFAULT current_timestamp,
    updated_at         TIMESTAMP DEFAULT current_timestamp,
    ended_at           TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    agent_id          VARCHAR NOT NULL REFERENCES agents(id),
    task_id           VARCHAR REFERENCES tasks(id),
    activity_id       VARCHAR REFERENCES activities(id),
    kind              VARCHAR NOT NULL
                         CHECK (kind IN ('receipt', 'completion', 'blocked', 'handoff', 'abandoned', 'task_update')),
    content           TEXT NOT NULL,
    source_channel    VARCHAR NOT NULL,
    policy            VARCHAR NOT NULL
                         CHECK (policy IN ('none', 'completion_blocked', 'all')),
    chat_visible      BOOLEAN DEFAULT TRUE,
    prompt_visibility BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS notification_links (
    notification_id VARCHAR PRIMARY KEY REFERENCES notifications(id),
    target_kind     VARCHAR NOT NULL
                       CHECK (target_kind IN ('desk')),
    target_path     VARCHAR NOT NULL,
    label           VARCHAR NOT NULL DEFAULT 'Open in Desk',
    created_at      TIMESTAMP DEFAULT current_timestamp
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
    id                  VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
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

-- ───────────────────────────────────────────────────────────────────────────
-- Diagnostic step trace — per-iteration turn detail for admin debugging
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS diagnostic_steps (
    id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    diagnostic_id     VARCHAR NOT NULL REFERENCES diagnostics(id),
    step_index        INTEGER NOT NULL,
    action_name       VARCHAR,
    context_snapshot  TEXT,
    raw_response      TEXT,
    parsed_action     TEXT,
    result            TEXT,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    duration_ms       INTEGER DEFAULT 0,
    error             TEXT,
    created_at        TIMESTAMP DEFAULT current_timestamp
);

-- ───────────────────────────────────────────────────────────────────────────
-- Telegram bot integration — session state per Telegram user
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS telegram_sessions (
    id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    telegram_user_id  BIGINT NOT NULL,
    session_type      VARCHAR NOT NULL DEFAULT 'idle'
                         CHECK (session_type IN ('dm', 'group', 'idle')),
    target_agent_id   VARCHAR REFERENCES agents(id),
    target_channel_id VARCHAR REFERENCES channels(id),
    agent_names_key   VARCHAR,
    last_active_at    TIMESTAMP DEFAULT current_timestamp,
    created_at        TIMESTAMP DEFAULT current_timestamp,
    updated_at        TIMESTAMP DEFAULT current_timestamp
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_sessions_user
    ON telegram_sessions (telegram_user_id);

-- ───────────────────────────────────────────────────────────────────────────
-- Indexes — high-traffic query patterns
-- ───────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_messages_from_agent_created
    ON messages (from_agent, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_to_agent_created
    ON messages (to_agent, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_triggers_agent_status
    ON agent_triggers (agent_id, status);

CREATE INDEX IF NOT EXISTS idx_runtime_commands_status_created
    ON runtime_commands (status, created_at);

CREATE INDEX IF NOT EXISTS idx_diagnostics_agent_created
    ON diagnostics (agent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_cli_policy_rules_tier_enabled
    ON cli_policy_rules (tier, enabled);

CREATE INDEX IF NOT EXISTS idx_cli_approval_requests_status
    ON cli_approval_requests (status, created_at);

CREATE INDEX IF NOT EXISTS idx_cli_approval_requests_agent
    ON cli_approval_requests (agent_id, status);

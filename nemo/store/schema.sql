-- 1) datasets
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id   VARCHAR PRIMARY KEY,
    name         VARCHAR NOT NULL,
    source_uri   VARCHAR NOT NULL,
    format       VARCHAR NOT NULL DEFAULT 'csv',
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    notes        VARCHAR,
    schema_json  VARCHAR
);

-- 2) insights
CREATE TABLE IF NOT EXISTS insights (
    insight_id             VARCHAR PRIMARY KEY,
    created_at             TIMESTAMP NOT NULL DEFAULT now(),
    run_id                 VARCHAR,
    thread_id              VARCHAR,
    title                  VARCHAR NOT NULL,
    question               VARCHAR NOT NULL,
    hypothesis_struct_json VARCHAR,
    sql                    VARCHAR NOT NULL,
    result_summary_json    VARCHAR NOT NULL,
    result_sample_json     VARCHAR,
    claim                  VARCHAR NOT NULL,
    claim_struct_json      VARCHAR,
    confidence             DOUBLE NOT NULL DEFAULT 0.5,
    effect_size            DOUBLE,
    coverage               DOUBLE,
    cost_ms                INTEGER,
    source_tables_json     VARCHAR,
    tags_json              VARCHAR,
    status                 VARCHAR NOT NULL DEFAULT 'ok',
    error_text             VARCHAR
);

-- 3) edges
CREATE TABLE IF NOT EXISTS edges (
    edge_id          VARCHAR PRIMARY KEY,
    created_at       TIMESTAMP NOT NULL DEFAULT now(),
    from_insight_id  VARCHAR NOT NULL REFERENCES insights(insight_id),
    to_insight_id    VARCHAR NOT NULL REFERENCES insights(insight_id),
    type             VARCHAR NOT NULL,
    weight           DOUBLE NOT NULL DEFAULT 0.5,
    rationale        VARCHAR
);

-- 4) frontier
CREATE TABLE IF NOT EXISTS frontier (
    action_id            VARCHAR PRIMARY KEY,
    created_at           TIMESTAMP NOT NULL DEFAULT now(),
    run_id               VARCHAR,
    thread_id            VARCHAR,
    action_type          VARCHAR NOT NULL,
    payload_json         VARCHAR NOT NULL,
    score                DOUBLE NOT NULL DEFAULT 0.0,
    status               VARCHAR NOT NULL DEFAULT 'queued',
    last_error           VARCHAR,
    depends_on_action_id VARCHAR,
    dedupe_key           VARCHAR NOT NULL
);

-- 5) runs
CREATE TABLE IF NOT EXISTS runs (
    run_id            VARCHAR PRIMARY KEY,
    started_at        TIMESTAMP NOT NULL DEFAULT now(),
    ended_at          TIMESTAMP,
    status            VARCHAR NOT NULL DEFAULT 'running',
    config_json       VARCHAR NOT NULL,
    steps_completed   INTEGER NOT NULL DEFAULT 0,
    insights_created  INTEGER NOT NULL DEFAULT 0,
    errors            INTEGER NOT NULL DEFAULT 0,
    frontier_size     INTEGER NOT NULL DEFAULT 0,
    notes             VARCHAR
);

-- 6) thread_cards
CREATE TABLE IF NOT EXISTS thread_cards (
    thread_id              VARCHAR PRIMARY KEY,
    updated_at             TIMESTAMP NOT NULL DEFAULT now(),
    title                  VARCHAR NOT NULL,
    summary_text           VARCHAR,
    key_insight_ids_json   VARCHAR,
    open_questions_json    VARCHAR,
    contradictions_json    VARCHAR
);

-- 7) learnings
CREATE TABLE IF NOT EXISTS learnings (
    learning_id      VARCHAR PRIMARY KEY,
    created_at       TIMESTAMP NOT NULL DEFAULT now(),
    run_id           VARCHAR,
    category         VARCHAR NOT NULL,
    subject          VARCHAR NOT NULL,
    detail           VARCHAR NOT NULL,
    confidence       DOUBLE NOT NULL DEFAULT 0.5,
    times_confirmed  INTEGER NOT NULL DEFAULT 1
);

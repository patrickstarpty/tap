CREATE DATABASE IF NOT EXISTS tap_insights;

CREATE TABLE IF NOT EXISTS tap_insights.report_batch_markers
(
    projection_version String,
    data_version UInt64,
    projection_batch_id FixedString(64),
    receipt_id UUID,
    project_id String,
    source_id String,
    external_run_id String,
    report_batch_id String,
    shard_id String,
    correction_no UInt32,
    application_commit String,
    script_commit String,
    environment String,
    configuration String,
    timezone String,
    raw_object_ref String,
    raw_checksum FixedString(64),
    parser_version String,
    payload_checksum FixedString(64),
    is_deleted UInt8,
    marker_checksum FixedString(64)
)
ENGINE = MergeTree
ORDER BY
(
    projection_version, project_id, source_id, external_run_id,
    report_batch_id, shard_id, correction_no, data_version,
    projection_batch_id
);

ALTER TABLE tap_insights.report_batch_markers
ADD COLUMN IF NOT EXISTS is_deleted UInt8 DEFAULT 0 AFTER payload_checksum;

CREATE TABLE IF NOT EXISTS tap_insights.attempt_facts
(
    projection_version String,
    data_version UInt64,
    projection_batch_id FixedString(64),
    receipt_id UUID,
    project_id String,
    source_id String,
    external_run_id String,
    report_batch_id String,
    shard_id String,
    correction_no UInt32,
    application_commit String,
    script_commit String,
    environment String,
    configuration String,
    timezone String,
    raw_object_ref String,
    raw_checksum FixedString(64),
    parser_version String,
    fact_key FixedString(64),
    fact_checksum FixedString(64),
    source_test_identity String,
    source_locator String,
    stable_test_id String,
    stable_test_id_present UInt8,
    data_row String,
    data_row_present UInt8,
    attempt Int64,
    attempt_present UInt8,
    result LowCardinality(String),
    duration_seconds Nullable(Float64),
    missing_reasons Array(String),
    first_attempt_eligible UInt8
)
ENGINE = MergeTree
ORDER BY
(
    projection_version, project_id, source_id, external_run_id,
    report_batch_id, shard_id, correction_no, fact_key,
    data_version, projection_batch_id
);

CREATE TABLE IF NOT EXISTS tap_insights.evidence_refs
(
    projection_version String,
    data_version UInt64,
    projection_batch_id FixedString(64),
    receipt_id UUID,
    project_id String,
    source_id String,
    external_run_id String,
    report_batch_id String,
    shard_id String,
    correction_no UInt32,
    application_commit String,
    script_commit String,
    environment String,
    configuration String,
    timezone String,
    fact_key FixedString(64),
    evidence_ref String,
    evidence_checksum FixedString(64)
)
ENGINE = MergeTree
ORDER BY
(
    projection_version, project_id, source_id, external_run_id,
    report_batch_id, shard_id, correction_no, fact_key,
    evidence_checksum, data_version
);

CREATE TABLE IF NOT EXISTS tap_insights.run_dimensions
(
    projection_version String,
    data_version UInt64,
    projection_batch_id FixedString(64),
    receipt_id UUID,
    project_id String,
    source_id String,
    external_run_id String,
    report_batch_id String,
    shard_id String,
    correction_no UInt32,
    application_commit String,
    script_commit String,
    environment String,
    configuration String,
    timezone String,
    job_id Nullable(String),
    build_id Nullable(String),
    branch Nullable(String),
    business_cycle_id Nullable(String),
    started_at Nullable(String),
    finished_at Nullable(String),
    dimension_checksum FixedString(64)
)
ENGINE = MergeTree
ORDER BY
(
    projection_version, project_id, source_id, external_run_id,
    report_batch_id, shard_id, correction_no, data_version,
    projection_batch_id
);

CREATE TABLE IF NOT EXISTS tap_insights.configuration_dimensions
(
    projection_version String,
    data_version UInt64,
    projection_batch_id FixedString(64),
    receipt_id UUID,
    project_id String,
    source_id String,
    external_run_id String,
    report_batch_id String,
    shard_id String,
    correction_no UInt32,
    application_commit String,
    script_commit String,
    environment String,
    configuration String,
    timezone String,
    configuration_key FixedString(64),
    dimension_checksum FixedString(64)
)
ENGINE = MergeTree
ORDER BY
(
    projection_version, project_id, configuration_key,
    data_version, projection_batch_id
);

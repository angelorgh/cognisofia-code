-- =============================================================================
-- NIRDuino / CogniSofIA — TimescaleDB Schema
-- Requires: PostgreSQL 14+ with TimescaleDB 2.x extension
--
-- Total columns in `frames`: 564
--   ts             : wall-clock timestamp        (1)
--   session_id     : FK → sessions               (1)
--   time_elapsed   : seconds since session start (1)
--   led_s{1..8}_*  : LED drive voltages (V)      (8 × 4 = 32)
--   s{1..8}_d{1..16}_* : detector readings (V)   (8 × 16 × 4 = 512)
--   dark_d{1..16}  : dark current (V)            (16)
--   stimulus       : marker (0=off, 10=on)       (1)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- =============================================================================
-- sessions — one row per recording session
-- =============================================================================
CREATE TABLE IF NOT EXISTS sessions (
    session_id    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_name  TEXT         NOT NULL,
    problem       TEXT         NOT NULL,
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ended_at      TIMESTAMPTZ,
    frames_count  INTEGER      NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_subject
    ON sessions (subject_name);

CREATE INDEX IF NOT EXISTS idx_sessions_problem
    ON sessions (problem);

-- =============================================================================
-- frames — per-frame fNIRS time-series data (hypertable)
-- =============================================================================
CREATE TABLE IF NOT EXISTS frames (
    ts              TIMESTAMPTZ      NOT NULL,
    session_id      UUID             NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    time_elapsed    DOUBLE PRECISION NOT NULL,

    -- ── LED drive voltages (V) — 8 sources × 4 modes ─────────────────────
    -- Source 1
    led_s1_740nm_rp  REAL,  led_s1_850nm_rp  REAL,  led_s1_740nm_lp  REAL,  led_s1_850nm_lp  REAL,
    -- Source 2
    led_s2_740nm_rp  REAL,  led_s2_850nm_rp  REAL,  led_s2_740nm_lp  REAL,  led_s2_850nm_lp  REAL,
    -- Source 3
    led_s3_740nm_rp  REAL,  led_s3_850nm_rp  REAL,  led_s3_740nm_lp  REAL,  led_s3_850nm_lp  REAL,
    -- Source 4
    led_s4_740nm_rp  REAL,  led_s4_850nm_rp  REAL,  led_s4_740nm_lp  REAL,  led_s4_850nm_lp  REAL,
    -- Source 5
    led_s5_740nm_rp  REAL,  led_s5_850nm_rp  REAL,  led_s5_740nm_lp  REAL,  led_s5_850nm_lp  REAL,
    -- Source 6
    led_s6_740nm_rp  REAL,  led_s6_850nm_rp  REAL,  led_s6_740nm_lp  REAL,  led_s6_850nm_lp  REAL,
    -- Source 7
    led_s7_740nm_rp  REAL,  led_s7_850nm_rp  REAL,  led_s7_740nm_lp  REAL,  led_s7_850nm_lp  REAL,
    -- Source 8
    led_s8_740nm_rp  REAL,  led_s8_850nm_rp  REAL,  led_s8_740nm_lp  REAL,  led_s8_850nm_lp  REAL,

    -- ── Detector readings (V) — 8 sources × 16 detectors × 4 modes ──────

    -- S1 · D1–D16
    s1_d1_740nm_rp   REAL,  s1_d1_850nm_rp   REAL,  s1_d1_740nm_lp   REAL,  s1_d1_850nm_lp   REAL,
    s1_d2_740nm_rp   REAL,  s1_d2_850nm_rp   REAL,  s1_d2_740nm_lp   REAL,  s1_d2_850nm_lp   REAL,
    s1_d3_740nm_rp   REAL,  s1_d3_850nm_rp   REAL,  s1_d3_740nm_lp   REAL,  s1_d3_850nm_lp   REAL,
    s1_d4_740nm_rp   REAL,  s1_d4_850nm_rp   REAL,  s1_d4_740nm_lp   REAL,  s1_d4_850nm_lp   REAL,
    s1_d5_740nm_rp   REAL,  s1_d5_850nm_rp   REAL,  s1_d5_740nm_lp   REAL,  s1_d5_850nm_lp   REAL,
    s1_d6_740nm_rp   REAL,  s1_d6_850nm_rp   REAL,  s1_d6_740nm_lp   REAL,  s1_d6_850nm_lp   REAL,
    s1_d7_740nm_rp   REAL,  s1_d7_850nm_rp   REAL,  s1_d7_740nm_lp   REAL,  s1_d7_850nm_lp   REAL,
    s1_d8_740nm_rp   REAL,  s1_d8_850nm_rp   REAL,  s1_d8_740nm_lp   REAL,  s1_d8_850nm_lp   REAL,
    s1_d9_740nm_rp   REAL,  s1_d9_850nm_rp   REAL,  s1_d9_740nm_lp   REAL,  s1_d9_850nm_lp   REAL,
    s1_d10_740nm_rp  REAL,  s1_d10_850nm_rp  REAL,  s1_d10_740nm_lp  REAL,  s1_d10_850nm_lp  REAL,
    s1_d11_740nm_rp  REAL,  s1_d11_850nm_rp  REAL,  s1_d11_740nm_lp  REAL,  s1_d11_850nm_lp  REAL,
    s1_d12_740nm_rp  REAL,  s1_d12_850nm_rp  REAL,  s1_d12_740nm_lp  REAL,  s1_d12_850nm_lp  REAL,
    s1_d13_740nm_rp  REAL,  s1_d13_850nm_rp  REAL,  s1_d13_740nm_lp  REAL,  s1_d13_850nm_lp  REAL,
    s1_d14_740nm_rp  REAL,  s1_d14_850nm_rp  REAL,  s1_d14_740nm_lp  REAL,  s1_d14_850nm_lp  REAL,
    s1_d15_740nm_rp  REAL,  s1_d15_850nm_rp  REAL,  s1_d15_740nm_lp  REAL,  s1_d15_850nm_lp  REAL,
    s1_d16_740nm_rp  REAL,  s1_d16_850nm_rp  REAL,  s1_d16_740nm_lp  REAL,  s1_d16_850nm_lp  REAL,

    -- S2 · D1–D16
    s2_d1_740nm_rp   REAL,  s2_d1_850nm_rp   REAL,  s2_d1_740nm_lp   REAL,  s2_d1_850nm_lp   REAL,
    s2_d2_740nm_rp   REAL,  s2_d2_850nm_rp   REAL,  s2_d2_740nm_lp   REAL,  s2_d2_850nm_lp   REAL,
    s2_d3_740nm_rp   REAL,  s2_d3_850nm_rp   REAL,  s2_d3_740nm_lp   REAL,  s2_d3_850nm_lp   REAL,
    s2_d4_740nm_rp   REAL,  s2_d4_850nm_rp   REAL,  s2_d4_740nm_lp   REAL,  s2_d4_850nm_lp   REAL,
    s2_d5_740nm_rp   REAL,  s2_d5_850nm_rp   REAL,  s2_d5_740nm_lp   REAL,  s2_d5_850nm_lp   REAL,
    s2_d6_740nm_rp   REAL,  s2_d6_850nm_rp   REAL,  s2_d6_740nm_lp   REAL,  s2_d6_850nm_lp   REAL,
    s2_d7_740nm_rp   REAL,  s2_d7_850nm_rp   REAL,  s2_d7_740nm_lp   REAL,  s2_d7_850nm_lp   REAL,
    s2_d8_740nm_rp   REAL,  s2_d8_850nm_rp   REAL,  s2_d8_740nm_lp   REAL,  s2_d8_850nm_lp   REAL,
    s2_d9_740nm_rp   REAL,  s2_d9_850nm_rp   REAL,  s2_d9_740nm_lp   REAL,  s2_d9_850nm_lp   REAL,
    s2_d10_740nm_rp  REAL,  s2_d10_850nm_rp  REAL,  s2_d10_740nm_lp  REAL,  s2_d10_850nm_lp  REAL,
    s2_d11_740nm_rp  REAL,  s2_d11_850nm_rp  REAL,  s2_d11_740nm_lp  REAL,  s2_d11_850nm_lp  REAL,
    s2_d12_740nm_rp  REAL,  s2_d12_850nm_rp  REAL,  s2_d12_740nm_lp  REAL,  s2_d12_850nm_lp  REAL,
    s2_d13_740nm_rp  REAL,  s2_d13_850nm_rp  REAL,  s2_d13_740nm_lp  REAL,  s2_d13_850nm_lp  REAL,
    s2_d14_740nm_rp  REAL,  s2_d14_850nm_rp  REAL,  s2_d14_740nm_lp  REAL,  s2_d14_850nm_lp  REAL,
    s2_d15_740nm_rp  REAL,  s2_d15_850nm_rp  REAL,  s2_d15_740nm_lp  REAL,  s2_d15_850nm_lp  REAL,
    s2_d16_740nm_rp  REAL,  s2_d16_850nm_rp  REAL,  s2_d16_740nm_lp  REAL,  s2_d16_850nm_lp  REAL,

    -- S3 · D1–D16
    s3_d1_740nm_rp   REAL,  s3_d1_850nm_rp   REAL,  s3_d1_740nm_lp   REAL,  s3_d1_850nm_lp   REAL,
    s3_d2_740nm_rp   REAL,  s3_d2_850nm_rp   REAL,  s3_d2_740nm_lp   REAL,  s3_d2_850nm_lp   REAL,
    s3_d3_740nm_rp   REAL,  s3_d3_850nm_rp   REAL,  s3_d3_740nm_lp   REAL,  s3_d3_850nm_lp   REAL,
    s3_d4_740nm_rp   REAL,  s3_d4_850nm_rp   REAL,  s3_d4_740nm_lp   REAL,  s3_d4_850nm_lp   REAL,
    s3_d5_740nm_rp   REAL,  s3_d5_850nm_rp   REAL,  s3_d5_740nm_lp   REAL,  s3_d5_850nm_lp   REAL,
    s3_d6_740nm_rp   REAL,  s3_d6_850nm_rp   REAL,  s3_d6_740nm_lp   REAL,  s3_d6_850nm_lp   REAL,
    s3_d7_740nm_rp   REAL,  s3_d7_850nm_rp   REAL,  s3_d7_740nm_lp   REAL,  s3_d7_850nm_lp   REAL,
    s3_d8_740nm_rp   REAL,  s3_d8_850nm_rp   REAL,  s3_d8_740nm_lp   REAL,  s3_d8_850nm_lp   REAL,
    s3_d9_740nm_rp   REAL,  s3_d9_850nm_rp   REAL,  s3_d9_740nm_lp   REAL,  s3_d9_850nm_lp   REAL,
    s3_d10_740nm_rp  REAL,  s3_d10_850nm_rp  REAL,  s3_d10_740nm_lp  REAL,  s3_d10_850nm_lp  REAL,
    s3_d11_740nm_rp  REAL,  s3_d11_850nm_rp  REAL,  s3_d11_740nm_lp  REAL,  s3_d11_850nm_lp  REAL,
    s3_d12_740nm_rp  REAL,  s3_d12_850nm_rp  REAL,  s3_d12_740nm_lp  REAL,  s3_d12_850nm_lp  REAL,
    s3_d13_740nm_rp  REAL,  s3_d13_850nm_rp  REAL,  s3_d13_740nm_lp  REAL,  s3_d13_850nm_lp  REAL,
    s3_d14_740nm_rp  REAL,  s3_d14_850nm_rp  REAL,  s3_d14_740nm_lp  REAL,  s3_d14_850nm_lp  REAL,
    s3_d15_740nm_rp  REAL,  s3_d15_850nm_rp  REAL,  s3_d15_740nm_lp  REAL,  s3_d15_850nm_lp  REAL,
    s3_d16_740nm_rp  REAL,  s3_d16_850nm_rp  REAL,  s3_d16_740nm_lp  REAL,  s3_d16_850nm_lp  REAL,

    -- S4 · D1–D16
    s4_d1_740nm_rp   REAL,  s4_d1_850nm_rp   REAL,  s4_d1_740nm_lp   REAL,  s4_d1_850nm_lp   REAL,
    s4_d2_740nm_rp   REAL,  s4_d2_850nm_rp   REAL,  s4_d2_740nm_lp   REAL,  s4_d2_850nm_lp   REAL,
    s4_d3_740nm_rp   REAL,  s4_d3_850nm_rp   REAL,  s4_d3_740nm_lp   REAL,  s4_d3_850nm_lp   REAL,
    s4_d4_740nm_rp   REAL,  s4_d4_850nm_rp   REAL,  s4_d4_740nm_lp   REAL,  s4_d4_850nm_lp   REAL,
    s4_d5_740nm_rp   REAL,  s4_d5_850nm_rp   REAL,  s4_d5_740nm_lp   REAL,  s4_d5_850nm_lp   REAL,
    s4_d6_740nm_rp   REAL,  s4_d6_850nm_rp   REAL,  s4_d6_740nm_lp   REAL,  s4_d6_850nm_lp   REAL,
    s4_d7_740nm_rp   REAL,  s4_d7_850nm_rp   REAL,  s4_d7_740nm_lp   REAL,  s4_d7_850nm_lp   REAL,
    s4_d8_740nm_rp   REAL,  s4_d8_850nm_rp   REAL,  s4_d8_740nm_lp   REAL,  s4_d8_850nm_lp   REAL,
    s4_d9_740nm_rp   REAL,  s4_d9_850nm_rp   REAL,  s4_d9_740nm_lp   REAL,  s4_d9_850nm_lp   REAL,
    s4_d10_740nm_rp  REAL,  s4_d10_850nm_rp  REAL,  s4_d10_740nm_lp  REAL,  s4_d10_850nm_lp  REAL,
    s4_d11_740nm_rp  REAL,  s4_d11_850nm_rp  REAL,  s4_d11_740nm_lp  REAL,  s4_d11_850nm_lp  REAL,
    s4_d12_740nm_rp  REAL,  s4_d12_850nm_rp  REAL,  s4_d12_740nm_lp  REAL,  s4_d12_850nm_lp  REAL,
    s4_d13_740nm_rp  REAL,  s4_d13_850nm_rp  REAL,  s4_d13_740nm_lp  REAL,  s4_d13_850nm_lp  REAL,
    s4_d14_740nm_rp  REAL,  s4_d14_850nm_rp  REAL,  s4_d14_740nm_lp  REAL,  s4_d14_850nm_lp  REAL,
    s4_d15_740nm_rp  REAL,  s4_d15_850nm_rp  REAL,  s4_d15_740nm_lp  REAL,  s4_d15_850nm_lp  REAL,
    s4_d16_740nm_rp  REAL,  s4_d16_850nm_rp  REAL,  s4_d16_740nm_lp  REAL,  s4_d16_850nm_lp  REAL,

    -- S5 · D1–D16
    s5_d1_740nm_rp   REAL,  s5_d1_850nm_rp   REAL,  s5_d1_740nm_lp   REAL,  s5_d1_850nm_lp   REAL,
    s5_d2_740nm_rp   REAL,  s5_d2_850nm_rp   REAL,  s5_d2_740nm_lp   REAL,  s5_d2_850nm_lp   REAL,
    s5_d3_740nm_rp   REAL,  s5_d3_850nm_rp   REAL,  s5_d3_740nm_lp   REAL,  s5_d3_850nm_lp   REAL,
    s5_d4_740nm_rp   REAL,  s5_d4_850nm_rp   REAL,  s5_d4_740nm_lp   REAL,  s5_d4_850nm_lp   REAL,
    s5_d5_740nm_rp   REAL,  s5_d5_850nm_rp   REAL,  s5_d5_740nm_lp   REAL,  s5_d5_850nm_lp   REAL,
    s5_d6_740nm_rp   REAL,  s5_d6_850nm_rp   REAL,  s5_d6_740nm_lp   REAL,  s5_d6_850nm_lp   REAL,
    s5_d7_740nm_rp   REAL,  s5_d7_850nm_rp   REAL,  s5_d7_740nm_lp   REAL,  s5_d7_850nm_lp   REAL,
    s5_d8_740nm_rp   REAL,  s5_d8_850nm_rp   REAL,  s5_d8_740nm_lp   REAL,  s5_d8_850nm_lp   REAL,
    s5_d9_740nm_rp   REAL,  s5_d9_850nm_rp   REAL,  s5_d9_740nm_lp   REAL,  s5_d9_850nm_lp   REAL,
    s5_d10_740nm_rp  REAL,  s5_d10_850nm_rp  REAL,  s5_d10_740nm_lp  REAL,  s5_d10_850nm_lp  REAL,
    s5_d11_740nm_rp  REAL,  s5_d11_850nm_rp  REAL,  s5_d11_740nm_lp  REAL,  s5_d11_850nm_lp  REAL,
    s5_d12_740nm_rp  REAL,  s5_d12_850nm_rp  REAL,  s5_d12_740nm_lp  REAL,  s5_d12_850nm_lp  REAL,
    s5_d13_740nm_rp  REAL,  s5_d13_850nm_rp  REAL,  s5_d13_740nm_lp  REAL,  s5_d13_850nm_lp  REAL,
    s5_d14_740nm_rp  REAL,  s5_d14_850nm_rp  REAL,  s5_d14_740nm_lp  REAL,  s5_d14_850nm_lp  REAL,
    s5_d15_740nm_rp  REAL,  s5_d15_850nm_rp  REAL,  s5_d15_740nm_lp  REAL,  s5_d15_850nm_lp  REAL,
    s5_d16_740nm_rp  REAL,  s5_d16_850nm_rp  REAL,  s5_d16_740nm_lp  REAL,  s5_d16_850nm_lp  REAL,

    -- S6 · D1–D16
    s6_d1_740nm_rp   REAL,  s6_d1_850nm_rp   REAL,  s6_d1_740nm_lp   REAL,  s6_d1_850nm_lp   REAL,
    s6_d2_740nm_rp   REAL,  s6_d2_850nm_rp   REAL,  s6_d2_740nm_lp   REAL,  s6_d2_850nm_lp   REAL,
    s6_d3_740nm_rp   REAL,  s6_d3_850nm_rp   REAL,  s6_d3_740nm_lp   REAL,  s6_d3_850nm_lp   REAL,
    s6_d4_740nm_rp   REAL,  s6_d4_850nm_rp   REAL,  s6_d4_740nm_lp   REAL,  s6_d4_850nm_lp   REAL,
    s6_d5_740nm_rp   REAL,  s6_d5_850nm_rp   REAL,  s6_d5_740nm_lp   REAL,  s6_d5_850nm_lp   REAL,
    s6_d6_740nm_rp   REAL,  s6_d6_850nm_rp   REAL,  s6_d6_740nm_lp   REAL,  s6_d6_850nm_lp   REAL,
    s6_d7_740nm_rp   REAL,  s6_d7_850nm_rp   REAL,  s6_d7_740nm_lp   REAL,  s6_d7_850nm_lp   REAL,
    s6_d8_740nm_rp   REAL,  s6_d8_850nm_rp   REAL,  s6_d8_740nm_lp   REAL,  s6_d8_850nm_lp   REAL,
    s6_d9_740nm_rp   REAL,  s6_d9_850nm_rp   REAL,  s6_d9_740nm_lp   REAL,  s6_d9_850nm_lp   REAL,
    s6_d10_740nm_rp  REAL,  s6_d10_850nm_rp  REAL,  s6_d10_740nm_lp  REAL,  s6_d10_850nm_lp  REAL,
    s6_d11_740nm_rp  REAL,  s6_d11_850nm_rp  REAL,  s6_d11_740nm_lp  REAL,  s6_d11_850nm_lp  REAL,
    s6_d12_740nm_rp  REAL,  s6_d12_850nm_rp  REAL,  s6_d12_740nm_lp  REAL,  s6_d12_850nm_lp  REAL,
    s6_d13_740nm_rp  REAL,  s6_d13_850nm_rp  REAL,  s6_d13_740nm_lp  REAL,  s6_d13_850nm_lp  REAL,
    s6_d14_740nm_rp  REAL,  s6_d14_850nm_rp  REAL,  s6_d14_740nm_lp  REAL,  s6_d14_850nm_lp  REAL,
    s6_d15_740nm_rp  REAL,  s6_d15_850nm_rp  REAL,  s6_d15_740nm_lp  REAL,  s6_d15_850nm_lp  REAL,
    s6_d16_740nm_rp  REAL,  s6_d16_850nm_rp  REAL,  s6_d16_740nm_lp  REAL,  s6_d16_850nm_lp  REAL,

    -- S7 · D1–D16
    s7_d1_740nm_rp   REAL,  s7_d1_850nm_rp   REAL,  s7_d1_740nm_lp   REAL,  s7_d1_850nm_lp   REAL,
    s7_d2_740nm_rp   REAL,  s7_d2_850nm_rp   REAL,  s7_d2_740nm_lp   REAL,  s7_d2_850nm_lp   REAL,
    s7_d3_740nm_rp   REAL,  s7_d3_850nm_rp   REAL,  s7_d3_740nm_lp   REAL,  s7_d3_850nm_lp   REAL,
    s7_d4_740nm_rp   REAL,  s7_d4_850nm_rp   REAL,  s7_d4_740nm_lp   REAL,  s7_d4_850nm_lp   REAL,
    s7_d5_740nm_rp   REAL,  s7_d5_850nm_rp   REAL,  s7_d5_740nm_lp   REAL,  s7_d5_850nm_lp   REAL,
    s7_d6_740nm_rp   REAL,  s7_d6_850nm_rp   REAL,  s7_d6_740nm_lp   REAL,  s7_d6_850nm_lp   REAL,
    s7_d7_740nm_rp   REAL,  s7_d7_850nm_rp   REAL,  s7_d7_740nm_lp   REAL,  s7_d7_850nm_lp   REAL,
    s7_d8_740nm_rp   REAL,  s7_d8_850nm_rp   REAL,  s7_d8_740nm_lp   REAL,  s7_d8_850nm_lp   REAL,
    s7_d9_740nm_rp   REAL,  s7_d9_850nm_rp   REAL,  s7_d9_740nm_lp   REAL,  s7_d9_850nm_lp   REAL,
    s7_d10_740nm_rp  REAL,  s7_d10_850nm_rp  REAL,  s7_d10_740nm_lp  REAL,  s7_d10_850nm_lp  REAL,
    s7_d11_740nm_rp  REAL,  s7_d11_850nm_rp  REAL,  s7_d11_740nm_lp  REAL,  s7_d11_850nm_lp  REAL,
    s7_d12_740nm_rp  REAL,  s7_d12_850nm_rp  REAL,  s7_d12_740nm_lp  REAL,  s7_d12_850nm_lp  REAL,
    s7_d13_740nm_rp  REAL,  s7_d13_850nm_rp  REAL,  s7_d13_740nm_lp  REAL,  s7_d13_850nm_lp  REAL,
    s7_d14_740nm_rp  REAL,  s7_d14_850nm_rp  REAL,  s7_d14_740nm_lp  REAL,  s7_d14_850nm_lp  REAL,
    s7_d15_740nm_rp  REAL,  s7_d15_850nm_rp  REAL,  s7_d15_740nm_lp  REAL,  s7_d15_850nm_lp  REAL,
    s7_d16_740nm_rp  REAL,  s7_d16_850nm_rp  REAL,  s7_d16_740nm_lp  REAL,  s7_d16_850nm_lp  REAL,

    -- S8 · D1–D16
    s8_d1_740nm_rp   REAL,  s8_d1_850nm_rp   REAL,  s8_d1_740nm_lp   REAL,  s8_d1_850nm_lp   REAL,
    s8_d2_740nm_rp   REAL,  s8_d2_850nm_rp   REAL,  s8_d2_740nm_lp   REAL,  s8_d2_850nm_lp   REAL,
    s8_d3_740nm_rp   REAL,  s8_d3_850nm_rp   REAL,  s8_d3_740nm_lp   REAL,  s8_d3_850nm_lp   REAL,
    s8_d4_740nm_rp   REAL,  s8_d4_850nm_rp   REAL,  s8_d4_740nm_lp   REAL,  s8_d4_850nm_lp   REAL,
    s8_d5_740nm_rp   REAL,  s8_d5_850nm_rp   REAL,  s8_d5_740nm_lp   REAL,  s8_d5_850nm_lp   REAL,
    s8_d6_740nm_rp   REAL,  s8_d6_850nm_rp   REAL,  s8_d6_740nm_lp   REAL,  s8_d6_850nm_lp   REAL,
    s8_d7_740nm_rp   REAL,  s8_d7_850nm_rp   REAL,  s8_d7_740nm_lp   REAL,  s8_d7_850nm_lp   REAL,
    s8_d8_740nm_rp   REAL,  s8_d8_850nm_rp   REAL,  s8_d8_740nm_lp   REAL,  s8_d8_850nm_lp   REAL,
    s8_d9_740nm_rp   REAL,  s8_d9_850nm_rp   REAL,  s8_d9_740nm_lp   REAL,  s8_d9_850nm_lp   REAL,
    s8_d10_740nm_rp  REAL,  s8_d10_850nm_rp  REAL,  s8_d10_740nm_lp  REAL,  s8_d10_850nm_lp  REAL,
    s8_d11_740nm_rp  REAL,  s8_d11_850nm_rp  REAL,  s8_d11_740nm_lp  REAL,  s8_d11_850nm_lp  REAL,
    s8_d12_740nm_rp  REAL,  s8_d12_850nm_rp  REAL,  s8_d12_740nm_lp  REAL,  s8_d12_850nm_lp  REAL,
    s8_d13_740nm_rp  REAL,  s8_d13_850nm_rp  REAL,  s8_d13_740nm_lp  REAL,  s8_d13_850nm_lp  REAL,
    s8_d14_740nm_rp  REAL,  s8_d14_850nm_rp  REAL,  s8_d14_740nm_lp  REAL,  s8_d14_850nm_lp  REAL,
    s8_d15_740nm_rp  REAL,  s8_d15_850nm_rp  REAL,  s8_d15_740nm_lp  REAL,  s8_d15_850nm_lp  REAL,
    s8_d16_740nm_rp  REAL,  s8_d16_850nm_rp  REAL,  s8_d16_740nm_lp  REAL,  s8_d16_850nm_lp  REAL,

    -- ── Dark current (V) — 16 detectors ──────────────────────────────────
    dark_d1   REAL,  dark_d2   REAL,  dark_d3   REAL,  dark_d4   REAL,
    dark_d5   REAL,  dark_d6   REAL,  dark_d7   REAL,  dark_d8   REAL,
    dark_d9   REAL,  dark_d10  REAL,  dark_d11  REAL,  dark_d12  REAL,
    dark_d13  REAL,  dark_d14  REAL,  dark_d15  REAL,  dark_d16  REAL,

    -- ── Stimulus marker ───────────────────────────────────────────────────
    stimulus  SMALLINT NOT NULL DEFAULT 0
);

-- Convert to TimescaleDB hypertable, partitioned by timestamp
-- 1-hour chunks suit a ~15 Hz frame rate (≈54 000 rows/hour before compression)
SELECT create_hypertable(
    'frames', 'ts',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists       => TRUE
);

-- Fast lookup of all frames belonging to a session, newest first
CREATE INDEX IF NOT EXISTS idx_frames_session_ts
    ON frames (session_id, ts DESC);

-- =============================================================================
-- Compression
-- TimescaleDB columnar compression typically achieves 90–95% space savings
-- on this kind of wide, repetitive sensor data.
-- segment_by session_id keeps each session's data contiguous on disk.
-- =============================================================================
ALTER TABLE frames SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'session_id',
    timescaledb.compress_orderby   = 'ts ASC'
);

-- Auto-compress chunks that are at least 1 hour old
SELECT add_compression_policy('frames', INTERVAL '1 hour', if_not_exists => TRUE);

-- =============================================================================
-- Helper: update sessions.frames_count and ended_at automatically
-- Call stop_session(session_id) from Python when streaming stops.
-- =============================================================================
CREATE OR REPLACE FUNCTION stop_session(p_session_id UUID)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    UPDATE sessions
    SET
        ended_at     = NOW(),
        frames_count = (
            SELECT COUNT(*) FROM frames WHERE session_id = p_session_id
        )
    WHERE session_id = p_session_id;
END;
$$;

-- =============================================================================
-- Useful queries (reference)
-- =============================================================================

-- All sessions for a subject:
--   SELECT * FROM sessions WHERE subject_name = 'John Doe' ORDER BY started_at DESC;

-- All frames for a session:
--   SELECT ts, time_elapsed, stimulus,
--          s1_d1_740nm_rp, s1_d1_850nm_rp, s1_d1_740nm_lp, s1_d1_850nm_lp
--   FROM frames
--   WHERE session_id = '<uuid>'
--   ORDER BY ts;

-- Average 740nm RP signal for S1-D1 across an entire session, in 5-second buckets:
--   SELECT time_bucket('5 seconds', ts) AS bucket,
--          AVG(s1_d1_740nm_rp) AS avg_740_rp
--   FROM frames
--   WHERE session_id = '<uuid>'
--   GROUP BY bucket
--   ORDER BY bucket;

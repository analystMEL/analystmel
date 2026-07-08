-- =============================================================
-- CVE (Contextual Valuation Engine) — Supabase schema
-- Paste this whole file into the Supabase SQL editor and Run.
-- Project: https://dhubgamhpzlygbdiarjt.supabase.co
--
-- Design notes:
--  * users.id is set to the Supabase Auth user id at signup
--    (the DEFAULT is only a fallback).
--  * RLS is intentionally NOT enabled yet — single-team phase.
--    Before opening to real users, enable RLS and add policies.
-- =============================================================

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    matrix_cell TEXT,
    stage_at_add INTEGER,
    alert_on_stage_change BOOLEAN DEFAULT FALSE,
    UNIQUE(user_id, ticker)
);

CREATE TABLE IF NOT EXISTS stage_change_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker TEXT NOT NULL,
    previous_stage INTEGER,
    new_stage INTEGER,
    changed_at TIMESTAMPTZ DEFAULT NOW(),
    matrix_cell TEXT
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_watchlist_user   ON watchlist (user_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_ticker ON watchlist (ticker);
CREATE INDEX IF NOT EXISTS idx_scl_changed_at   ON stage_change_log (changed_at DESC);

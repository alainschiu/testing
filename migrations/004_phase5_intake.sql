-- Phase 5a: unified intake. Every source (scout_agent, watchers, RSS, email,
-- manual capture) funnels into raw_findings. The normaliser agent consumes
-- pending rows and produces canonical opportunities. The watchers table is
-- created here too so Phase 5b is purely additive code; it stays empty until
-- 5b populates it.

CREATE TABLE raw_findings (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,                  -- 'scout_agent' | 'watcher:<name>' | 'rss:<name>' | 'email' | 'manual' | 'legacy_scout'
  source_url TEXT,
  raw_text TEXT NOT NULL,
  raw_html TEXT,
  captured_at TEXT NOT NULL,
  processed_at TEXT,
  status TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'normalised' | 'rejected' | 'duplicate' | 'error'
  reject_reason TEXT,                     -- 'not_an_opportunity' | 'ineligible' | 'duplicate' | 'malformed'
  opportunity_id INTEGER REFERENCES opportunities(id),
  source_meta_json TEXT,
  cost_usd REAL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_rf_status ON raw_findings(status);
CREATE INDEX idx_rf_captured ON raw_findings(captured_at DESC);
CREATE INDEX idx_rf_source ON raw_findings(source);

CREATE TABLE watchers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  url TEXT NOT NULL,
  kind TEXT NOT NULL,                    -- 'html_static' | 'html_js' | 'rss' | 'json_api'
  selector TEXT,
  schedule_cron TEXT NOT NULL,
  headers_json TEXT,
  last_checked_at TEXT,
  last_content_hash TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_watcher_active ON watchers(active);

ALTER TABLE opportunities ADD COLUMN raw_finding_id INTEGER REFERENCES raw_findings(id);

-- Backfill: synthesise a raw_finding for every pre-existing opportunity so
-- the audit trail is complete. Source = 'legacy_scout' so it's distinguishable
-- from new scout-agent findings. Idempotent: we only touch opportunities with
-- raw_finding_id IS NULL, and the migration runner won't re-run this file.
INSERT INTO raw_findings
  (source, source_url, raw_text, captured_at, processed_at, status, opportunity_id, source_meta_json)
SELECT
  'legacy_scout',
  url,
  COALESCE(raw_finding_json, ''),
  discovered_at,
  discovered_at,
  'normalised',
  id,
  '{"legacy": true}'
FROM opportunities
WHERE raw_finding_id IS NULL;

UPDATE opportunities
   SET raw_finding_id = (
     SELECT id FROM raw_findings rf
      WHERE rf.opportunity_id = opportunities.id
        AND rf.source = 'legacy_scout'
      LIMIT 1
   )
 WHERE raw_finding_id IS NULL;

-- Phase 3: past-application exemplars + hallucination flags on drafts.

CREATE TABLE past_applications (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,           -- residency | grant | commission | ...
  funder TEXT,
  result TEXT NOT NULL,         -- won | shortlisted
  excerpt TEXT NOT NULL,        -- normalised text body (truncated)
  source_path TEXT,
  opportunity_id INTEGER REFERENCES opportunities(id),
  imported_at TEXT NOT NULL
);

CREATE INDEX idx_past_apps_type ON past_applications(type, result);

ALTER TABLE drafts ADD COLUMN hallucination_flags TEXT;  -- JSON array of strings
ALTER TABLE drafts ADD COLUMN critique TEXT;             -- critic output (for revision rounds)

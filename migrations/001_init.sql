-- Phase 0 initial schema. Reorder vs. the brief so FK targets exist first;
-- field set is otherwise verbatim from the build brief §3.

CREATE TABLE artist_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  data_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE funders (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  country TEXT,
  website TEXT,
  notes TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  opportunities_found INTEGER DEFAULT 0,
  opportunities_added INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  log_path TEXT,
  error TEXT
);

CREATE TABLE opportunities (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  funder_id INTEGER REFERENCES funders(id),
  type TEXT NOT NULL,
  url TEXT NOT NULL,
  url_hash TEXT NOT NULL UNIQUE,
  deadline TEXT,
  deadline_note TEXT,
  location TEXT,
  amount TEXT,
  duration TEXT,
  eligibility_citizenship TEXT,
  eligibility_career_stage TEXT,
  eligibility_other TEXT,
  fit_score INTEGER,
  primary_angle TEXT,
  backup_angle TEXT,
  why_fits TEXT,
  risk_watchout TEXT,
  effort_estimate TEXT,
  competitiveness TEXT,
  raw_finding_json TEXT,
  source_run_id INTEGER REFERENCES runs(id),
  status TEXT NOT NULL DEFAULT 'lead',
  status_changed_at TEXT,
  user_notes TEXT,
  discovered_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_opp_deadline ON opportunities(deadline);
CREATE INDEX idx_opp_status ON opportunities(status);
CREATE INDEX idx_opp_fit ON opportunities(fit_score DESC);

CREATE TABLE applications (
  id INTEGER PRIMARY KEY,
  opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  status TEXT NOT NULL,
  submitted_at TEXT,
  result TEXT,
  result_at TEXT,
  user_notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE drafts (
  id INTEGER PRIMARY KEY,
  application_id INTEGER NOT NULL REFERENCES applications(id),
  kind TEXT NOT NULL,
  variant TEXT,
  content TEXT NOT NULL,
  word_count INTEGER,
  model TEXT NOT NULL,
  prompt_template TEXT NOT NULL,
  parent_draft_id INTEGER REFERENCES drafts(id),
  cost_usd REAL,
  created_at TEXT NOT NULL
);

CREATE TABLE work_samples (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  year INTEGER,
  angle_tags TEXT NOT NULL,
  description TEXT,
  url TEXT,
  file_path TEXT,
  duration_seconds INTEGER,
  created_at TEXT NOT NULL
);

-- Phase 2: audit trail for status transitions + recent-application links.

CREATE TABLE status_audit (
  id INTEGER PRIMARY KEY,
  opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  from_status TEXT,
  to_status TEXT NOT NULL,
  at TEXT NOT NULL,
  note TEXT
);

CREATE INDEX idx_audit_opp ON status_audit(opportunity_id, at DESC);

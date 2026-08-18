CREATE TABLE identities(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, provider TEXT NOT NULL, external_id TEXT NOT NULL, revoked_at TEXT NULL);

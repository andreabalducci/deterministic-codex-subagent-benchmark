CREATE UNIQUE INDEX ux_identities_active_provider_external
ON identities(provider, external_id)
WHERE revoked_at IS NULL;
CREATE INDEX ix_identities_account ON identities(account_id);

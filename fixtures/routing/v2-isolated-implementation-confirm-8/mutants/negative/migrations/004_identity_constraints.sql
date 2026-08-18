CREATE UNIQUE INDEX ux_identities_provider_external
ON identities(provider, external_id);
CREATE INDEX ix_identities_account ON identities(account_id);

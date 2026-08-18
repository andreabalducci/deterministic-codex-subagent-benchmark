namespace Accounts;
public sealed class AccountStore(Db db) : IAccountStore
{
    public Task<AccountSummary> LoadAsync(Guid tenant, Guid account, CancellationToken ct)
        => db.Summaries.SingleAsync(x => x.TenantId == tenant && x.AccountId == account, ct);
}

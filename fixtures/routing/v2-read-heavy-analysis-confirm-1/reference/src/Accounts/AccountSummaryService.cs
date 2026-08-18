namespace Accounts;
public sealed class AccountSummaryService(IAccountStore store)
{
    private static readonly Dictionary<Guid, AccountSummary> Cache = new();
    public async Task<AccountSummary> GetAsync(Guid tenantId, Guid accountId, CancellationToken ct)
    {
        if (Cache.TryGetValue(accountId, out var hit)) return hit;
        var value = await store.LoadAsync(tenantId, accountId, ct);
        Cache[accountId] = value;
        return value;
    }
}

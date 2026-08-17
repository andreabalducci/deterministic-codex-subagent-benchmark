using System.Collections.Concurrent;

namespace Cache.Core;

public sealed class AsyncExpiringCache<TKey, TValue> where TKey : notnull
{
    private readonly ConcurrentDictionary<TKey, Task<Entry>> _entries = new();
    private readonly TimeProvider _timeProvider;
    private readonly TimeSpan _timeToLive;

    public AsyncExpiringCache(TimeProvider timeProvider, TimeSpan timeToLive)
    {
        _timeProvider = timeProvider;
        _timeToLive = timeToLive;
    }

    public async ValueTask<TValue> GetAsync(
        TKey key,
        Func<TKey, CancellationToken, ValueTask<TValue>> factory,
        CancellationToken cancellationToken = default)
    {
        var load = _entries.GetOrAdd(key, candidate => LoadAsync(candidate, factory, cancellationToken));
        var entry = await load.WaitAsync(cancellationToken);

        if (entry.ExpiresAt <= _timeProvider.GetUtcNow())
        {
            _entries.TryRemove(key, out _);
            return await GetAsync(key, factory, cancellationToken);
        }

        return entry.Value;
    }

    public bool Invalidate(TKey key) => _entries.TryRemove(key, out _);

    private async Task<Entry> LoadAsync(
        TKey key,
        Func<TKey, CancellationToken, ValueTask<TValue>> factory,
        CancellationToken cancellationToken)
    {
        var value = await factory(key, cancellationToken);
        return new Entry(value, _timeProvider.GetUtcNow() + _timeToLive);
    }

    private sealed record Entry(TValue Value, DateTimeOffset ExpiresAt);
}

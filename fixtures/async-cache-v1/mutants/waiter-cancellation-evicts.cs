using System.Collections.Concurrent;

namespace Cache.Core;

public sealed class AsyncExpiringCache<TKey, TValue> where TKey : notnull
{
    private readonly ConcurrentDictionary<TKey, Slot> _entries = new();
    private readonly TimeProvider _timeProvider;
    private readonly TimeSpan _timeToLive;

    public AsyncExpiringCache(TimeProvider timeProvider, TimeSpan timeToLive)
    {
        _timeProvider = timeProvider ?? throw new ArgumentNullException(nameof(timeProvider));
        if (timeToLive <= TimeSpan.Zero)
            throw new ArgumentOutOfRangeException(nameof(timeToLive));
        _timeToLive = timeToLive;
    }

    public async ValueTask<TValue> GetAsync(
        TKey key,
        Func<TKey, CancellationToken, ValueTask<TValue>> factory,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(factory);
        while (true)
        {
            var slot = _entries.GetOrAdd(key, candidate => new Slot(LoadAsync(candidate, factory)));
            try
            {
                var entry = await slot.Load.WaitAsync(cancellationToken).ConfigureAwait(false);
                if (entry.ExpiresAt > _timeProvider.GetUtcNow())
                    return entry.Value;
                _entries.TryRemove(new KeyValuePair<TKey, Slot>(key, slot));
            }
            catch
            {
                _entries.TryRemove(new KeyValuePair<TKey, Slot>(key, slot));
                throw;
            }
        }
    }

    public bool Invalidate(TKey key) => _entries.TryRemove(key, out _);

    private async Task<Entry> LoadAsync(
        TKey key,
        Func<TKey, CancellationToken, ValueTask<TValue>> factory)
    {
        var value = await factory(key, CancellationToken.None).ConfigureAwait(false);
        return new Entry(value, _timeProvider.GetUtcNow() + _timeToLive);
    }

    private sealed class Slot(Task<Entry> load)
    {
        public Task<Entry> Load { get; } = load;
    }

    private sealed record Entry(TValue Value, DateTimeOffset ExpiresAt);
}

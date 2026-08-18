using System.Collections.Concurrent;

namespace Cache.Core;

public sealed class AsyncExpiringCache<TKey, TValue> where TKey : notnull
{
    private readonly ConcurrentDictionary<TKey, Slot> _entries = new();
    private readonly TimeProvider _timeProvider;
    private readonly TimeSpan _timeToLive;

    public AsyncExpiringCache(TimeProvider timeProvider, TimeSpan timeToLive)
    {
        ArgumentNullException.ThrowIfNull(timeProvider);
        if (timeToLive <= TimeSpan.Zero)
            throw new ArgumentOutOfRangeException(nameof(timeToLive));

        _timeProvider = timeProvider;
        _timeToLive = timeToLive;
    }

    public ValueTask<TValue> GetAsync(
        TKey key,
        Func<TKey, CancellationToken, ValueTask<TValue>> factory,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(factory);
        var slot = _entries.GetOrAdd(key, static _ => new Slot());
        Task<TValue> shared;
        TaskCompletionSource<TValue>? source = null;

        lock (slot.Gate)
        {
            if (slot.HasValue && _timeProvider.GetUtcNow() < slot.ExpiresAt)
                return ValueTask.FromResult(slot.Value!);

            if (slot.Loading is null)
            {
                source = new(TaskCreationOptions.RunContinuationsAsynchronously);
                slot.Loading = source.Task;
            }

            shared = slot.Loading;
        }

        if (source is not null)
            _ = CompleteLoadAsync(slot, key, factory, source);

        return new ValueTask<TValue>(shared.WaitAsync(cancellationToken));
    }

    public bool Invalidate(TKey key) => _entries.TryRemove(key, out _);

    private async Task CompleteLoadAsync(
        Slot slot,
        TKey key,
        Func<TKey, CancellationToken, ValueTask<TValue>> factory,
        TaskCompletionSource<TValue> source)
    {
        try
        {
            var value = await factory(key, CancellationToken.None).ConfigureAwait(false);
            lock (slot.Gate)
            {
                if (ReferenceEquals(slot.Loading, source.Task))
                {
                    slot.Value = value;
                    slot.HasValue = true;
                    slot.ExpiresAt = _timeProvider.GetUtcNow() + _timeToLive;
                    slot.Loading = null;
                }
            }

            // The completed old slot is put back unconditionally, allowing an
            // invalidated generation to replace a newer slot for the same key.
            _entries[key] = slot;
            source.TrySetResult(value);
        }
        catch (Exception exception)
        {
            lock (slot.Gate)
            {
                if (ReferenceEquals(slot.Loading, source.Task))
                    slot.Loading = null;
            }
            source.TrySetException(exception);
        }
    }

    private sealed class Slot
    {
        public object Gate { get; } = new();
        public Task<TValue>? Loading { get; set; }
        public TValue? Value { get; set; }
        public bool HasValue { get; set; }
        public DateTimeOffset ExpiresAt { get; set; }
    }
}

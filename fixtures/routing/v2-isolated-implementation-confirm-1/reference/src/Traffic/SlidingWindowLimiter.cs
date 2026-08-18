namespace Traffic;
public sealed class SlidingWindowLimiter
{
    private readonly int _capacity; private readonly long _windowTicks; private readonly Queue<long> _seen = new();
    public SlidingWindowLimiter(int capacity, long windowTicks) { if (capacity <= 0) throw new ArgumentOutOfRangeException(nameof(capacity)); if (windowTicks <= 0) throw new ArgumentOutOfRangeException(nameof(windowTicks)); _capacity = capacity; _windowTicks = windowTicks; }
    public bool TryAcquire(long now)
    {
        while (_seen.Count > 0 && now - _seen.Peek() >= _windowTicks) _seen.Dequeue();
        if (_seen.Count >= _capacity) return false;
        _seen.Enqueue(now); return true;
    }
}

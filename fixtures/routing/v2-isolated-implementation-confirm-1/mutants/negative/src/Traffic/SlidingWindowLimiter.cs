namespace Traffic;
public sealed class SlidingWindowLimiter(int capacity, long windowTicks)
{
    private readonly Queue<long> _seen = new();
    public bool TryAcquire(long now)
    {
        while (_seen.Count > 0 && now - _seen.Peek() >= windowTicks) _seen.Dequeue();
        if (_seen.Count > capacity) return false;
        _seen.Enqueue(now); return true;
    }
}

using Traffic;
var limiter = new SlidingWindowLimiter(2, 10);
Check(limiter.TryAcquire(0), "first"); Check(limiter.TryAcquire(1), "second"); Check(!limiter.TryAcquire(2), "capacity"); Check(limiter.TryAcquire(10), "boundary eviction");
try { _ = new SlidingWindowLimiter(0, 10); throw new Exception("zero accepted"); } catch (ArgumentOutOfRangeException) {}
static void Check(bool ok, string name) { if (!ok) throw new Exception(name); }

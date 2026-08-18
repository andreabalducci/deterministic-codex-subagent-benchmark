using Cache.Core;

var tests = new (string Name, Func<Task> Run)[]
{
    ("constructor validation", ConstructorValidation),
    ("same-key misses are single flight", SameKeySingleFlight),
    ("different keys load concurrently", DifferentKeysOverlap),
    ("TTL starts at successful completion and expires at boundary", ExpirationBoundary),
    ("failed load is retried", FailureIsRetried),
    ("one cancelled waiter does not cancel shared load", CancelledWaiterIsIsolated),
    ("all waiters may cancel while successful load remains cached", AbandonedLoadIsCached),
    ("invalidation supersedes an older in-flight generation", InvalidateInFlight),
};

const int TestTimeoutSeconds = 5;
var failures = 0;
var timedOut = false;
foreach (var (name, run) in tests)
{
    try
    {
        var test = run();
        ObserveFault(test);
        await test.WaitAsync(TimeSpan.FromSeconds(TestTimeoutSeconds));
        Console.WriteLine($"PASS {name}");
    }
    catch (TimeoutException)
    {
        failures++;
        timedOut = true;
        Console.WriteLine($"FAIL {name}: TimeoutException: exceeded the {TestTimeoutSeconds}-second test timeout");
    }
    catch (Exception exception)
    {
        failures++;
        Console.WriteLine($"FAIL {name}: {exception.GetType().Name}: {exception.Message}");
    }

    // A timed-out test may still have an uncancellable candidate task in flight. Every
    // test currently owns its state, but candidate implementations may use static state;
    // do not start another behavior while that orphaned task could affect it.
    if (timedOut) break;
}

if (failures == 0) Console.WriteLine("CODEX_BENCH_HIDDEN_PASS_V1");
return failures == 0 ? 0 : 1;

static Task ConstructorValidation()
{
    Throws<ArgumentNullException>(() => new AsyncExpiringCache<string, int>(null!, TimeSpan.FromSeconds(1)));
    Throws<ArgumentOutOfRangeException>(() => new AsyncExpiringCache<string, int>(TimeProvider.System, TimeSpan.Zero));
    Throws<ArgumentOutOfRangeException>(() => new AsyncExpiringCache<string, int>(TimeProvider.System, TimeSpan.FromTicks(-1)));
    return Task.CompletedTask;
}

static async Task SameKeySingleFlight()
{
    var cache = NewCache<string, int>();
    const int callerCount = 32;
    var start = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var allInvoked = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var calls = 0;
    var invoked = 0;
    async ValueTask<int> Factory(string _, CancellationToken token)
    {
        Equal(CancellationToken.None, token);
        Interlocked.Increment(ref calls);
        entered.TrySetResult();
        await release.Task;
        return 42;
    }

    async Task<int> Caller()
    {
        await start.Task;
        var pending = cache.GetAsync("same", Factory).AsTask();
        if (Interlocked.Increment(ref invoked) == callerCount)
            allInvoked.TrySetResult();
        return await pending;
    }

    var callers = Enumerable.Range(0, callerCount).Select(_ => Caller()).ToArray();
    start.SetResult();
    await Task.WhenAll(entered.Task, allInvoked.Task);
    Equal(1, Volatile.Read(ref calls));
    release.SetResult();
    var values = await Task.WhenAll(callers);
    True(values.All(value => value == 42));
    Equal(1, calls);
}

static async Task DifferentKeysOverlap()
{
    var cache = NewCache<string, string>();
    var bothEntered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var entered = 0;
    async ValueTask<string> Factory(string key, CancellationToken _)
    {
        if (Interlocked.Increment(ref entered) == 2) bothEntered.SetResult();
        await release.Task;
        return key;
    }

    var first = cache.GetAsync("a", Factory).AsTask();
    var second = cache.GetAsync("b", Factory).AsTask();
    await bothEntered.Task;
    release.SetResult();
    Equal("a", await first);
    Equal("b", await second);
}

static async Task ExpirationBoundary()
{
    var time = new ManualTimeProvider(new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero));
    var cache = new AsyncExpiringCache<string, int>(time, TimeSpan.FromSeconds(10));
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var calls = 0;
    async ValueTask<int> Factory(string _, CancellationToken __)
    {
        var call = Interlocked.Increment(ref calls);
        if (call == 1) await release.Task;
        return call;
    }

    var first = cache.GetAsync("key", Factory).AsTask();
    time.Advance(TimeSpan.FromHours(1));
    release.SetResult();
    Equal(1, await first);
    time.Advance(TimeSpan.FromSeconds(9));
    Equal(1, await cache.GetAsync("key", Factory));
    time.Advance(TimeSpan.FromSeconds(1));
    Equal(2, await cache.GetAsync("key", Factory));
}

static async Task FailureIsRetried()
{
    var cache = NewCache<string, int>();
    var calls = 0;
    async ValueTask<int> Factory(string _, CancellationToken __)
    {
        await Task.Yield();
        if (Interlocked.Increment(ref calls) == 1) throw new InvalidOperationException("boom");
        return 7;
    }

    await ThrowsAsync<InvalidOperationException>(() => cache.GetAsync("key", Factory).AsTask());
    Equal(7, await cache.GetAsync("key", Factory));
    Equal(2, calls);
}

static async Task CancelledWaiterIsIsolated()
{
    var cache = NewCache<string, int>();
    var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var calls = 0;
    async ValueTask<int> Factory(string _, CancellationToken token)
    {
        Equal(CancellationToken.None, token);
        Interlocked.Increment(ref calls);
        entered.SetResult();
        await release.Task;
        return 9;
    }

    using var cancellation = new CancellationTokenSource();
    var cancelled = cache.GetAsync("key", Factory, cancellation.Token).AsTask();
    await entered.Task;
    var survivor = cache.GetAsync("key", Factory).AsTask();
    cancellation.Cancel();
    await ThrowsAsync<OperationCanceledException>(() => cancelled);
    release.SetResult();
    Equal(9, await survivor);
    Equal(1, calls);
}

static async Task AbandonedLoadIsCached()
{
    var cache = NewCache<string, int>();
    var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var calls = 0;
    async ValueTask<int> Factory(string _, CancellationToken __)
    {
        Interlocked.Increment(ref calls);
        entered.SetResult();
        await release.Task;
        return 11;
    }

    using var cancellation = new CancellationTokenSource();
    var abandoned = cache.GetAsync("key", Factory, cancellation.Token).AsTask();
    await entered.Task;
    cancellation.Cancel();
    await ThrowsAsync<OperationCanceledException>(() => abandoned);
    release.SetResult();
    Equal(11, await cache.GetAsync("key", Factory));
    Equal(1, calls);
}

static async Task InvalidateInFlight()
{
    var cache = NewCache<string, string>();
    var oldEntered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var releaseOld = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var calls = 0;
    async ValueTask<string> Factory(string _, CancellationToken __)
    {
        var call = Interlocked.Increment(ref calls);
        if (call == 1)
        {
            oldEntered.SetResult();
            await releaseOld.Task;
            return "old";
        }
        return "new";
    }

    var oldCaller = cache.GetAsync("key", Factory).AsTask();
    await oldEntered.Task;
    True(cache.Invalidate("key"));
    Equal("new", await cache.GetAsync("key", Factory));
    releaseOld.SetResult();
    Equal("old", await oldCaller);
    Equal("new", await cache.GetAsync("key", Factory));
    Equal(2, calls);
}

static AsyncExpiringCache<TKey, TValue> NewCache<TKey, TValue>() where TKey : notnull =>
    new(TimeProvider.System, TimeSpan.FromMinutes(5));

static void Equal<T>(T expected, T actual)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
        throw new InvalidOperationException($"Expected {expected}; got {actual}");
}

static void True(bool condition)
{
    if (!condition) throw new InvalidOperationException("Expected true");
}

static void Throws<TException>(Action action) where TException : Exception
{
    try { action(); }
    catch (TException) { return; }
    throw new InvalidOperationException($"Expected {typeof(TException).Name}");
}

static async Task ThrowsAsync<TException>(Func<Task> action) where TException : Exception
{
    try { await action(); }
    catch (TException) { return; }
    throw new InvalidOperationException($"Expected {typeof(TException).Name}");
}

static void ObserveFault(Task task)
{
    _ = task.ContinueWith(
        completed => _ = completed.Exception,
        CancellationToken.None,
        TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously,
        TaskScheduler.Default);
}

sealed class ManualTimeProvider(DateTimeOffset utcNow) : TimeProvider
{
    private long _utcTicks = utcNow.UtcTicks;
    public override DateTimeOffset GetUtcNow() => new(Interlocked.Read(ref _utcTicks), TimeSpan.Zero);
    public void Advance(TimeSpan delta) => Interlocked.Add(ref _utcTicks, delta.Ticks);
}

using Cache.Core;

var tests = new (string Name, Func<Task> Run)[]
{
    ("miss loads and hit reuses value", MissThenHit),
    ("invalidate forces reload", InvalidateForcesReload),
    ("blank cancellation leaves normal calls usable", DefaultCancellation),
};

var failures = 0;
foreach (var (name, run) in tests)
{
    try
    {
        await run();
        Console.WriteLine($"PASS {name}");
    }
    catch (Exception exception)
    {
        failures++;
        Console.WriteLine($"FAIL {name}: {exception.GetType().Name}: {exception.Message}");
    }
}

if (failures == 0) Console.WriteLine("CODEX_BENCH_PUBLIC_PASS_V1");
return failures == 0 ? 0 : 1;

static async Task MissThenHit()
{
    var cache = new AsyncExpiringCache<string, int>(TimeProvider.System, TimeSpan.FromMinutes(1));
    var calls = 0;
    ValueTask<int> Factory(string _, CancellationToken __) => ValueTask.FromResult(++calls);

    Equal(1, await cache.GetAsync("alpha", Factory));
    Equal(1, await cache.GetAsync("alpha", Factory));
    Equal(1, calls);
}

static async Task InvalidateForcesReload()
{
    var cache = new AsyncExpiringCache<string, int>(TimeProvider.System, TimeSpan.FromMinutes(1));
    var calls = 0;
    ValueTask<int> Factory(string _, CancellationToken __) => ValueTask.FromResult(++calls);

    Equal(1, await cache.GetAsync("alpha", Factory));
    True(cache.Invalidate("alpha"));
    Equal(2, await cache.GetAsync("alpha", Factory));
    True(!cache.Invalidate("missing"));
}

static async Task DefaultCancellation()
{
    var cache = new AsyncExpiringCache<string, string>(TimeProvider.System, TimeSpan.FromMinutes(1));
    var value = await cache.GetAsync("key", static (key, _) => ValueTask.FromResult(key.ToUpperInvariant()));
    Equal("KEY", value);
}

static void Equal<T>(T expected, T actual)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
        throw new InvalidOperationException($"Expected {expected}; got {actual}");
}

static void True(bool condition)
{
    if (!condition) throw new InvalidOperationException("Expected true");
}

# Repair the asynchronous expiring cache

Fix `Cache.Core/AsyncExpiringCache.cs` without changing its public API.

## Required behavior

- Constructor arguments `timeProvider` and `timeToLive` must reject `null` and non-positive TTL values respectively.
- A non-expired hit returns the stored value without invoking the supplied factory.
- Concurrent misses for the same key share exactly one in-flight factory call.
- Misses for different keys can run concurrently.
- TTL begins when a factory completes successfully. An entry is expired when current time is equal to or later than its expiry.
- A failed factory call is never cached; the next call retries.
- Caller cancellation cancels only that caller's wait. It must not cancel a shared factory or other waiters. The shared factory must receive `CancellationToken.None`.
- If every current waiter cancels, the shared factory may still finish and its successful result must remain cached.
- `Invalidate(key)` returns whether an entry or in-flight load existed and ensures a later call starts a fresh load. An older in-flight load must not replace the new generation.

## Constraints

- Keep the implementation thread-safe and generic.
- Do not use `.Result`, `.Wait()`, `GetAwaiter().GetResult()`, `Thread.Sleep`, `Task.Delay`, busy polling, sleeps, or other blocking waits. A short synchronous critical section that never runs user code is allowed.
- Do not terminate or launch processes, add module initializers or native interop, or dynamically load assemblies (`Environment.Exit`/`FailFast`, `Process.Start`, `ModuleInitializer`, `DllImport`/`LibraryImport`, or `Assembly.Load*`).
- Do not add packages or access the network.
- Work only inside the assigned directory and do not inspect sibling benchmark runs or hidden evaluators.
- Run `dotnet run --project Cache.PublicTests/Cache.PublicTests.csproj` before reporting completion.
- Return a concise summary, commands run, and total elapsed wall time.

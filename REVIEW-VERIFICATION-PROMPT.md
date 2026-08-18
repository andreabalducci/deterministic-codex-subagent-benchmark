# Review verification prompt

Paste the section below into a fresh session to independently audit the code review of this
repository. It is written adversarially: it asks for refutation rather than confirmation, marks
one claim that was investigated and withdrawn (B4), one that was never verified at all (A25), and
one that cannot be settled on macOS (section D).

---

You are auditing a code review that another model produced. Treat every claim below as
UNVERIFIED. Your job is to confirm or refute each one from the code and from experiments —
not to agree with it. The review may contain errors, overstatements, and miscounted line
numbers. Report those.

Repo: /Users/andrea/dev/github/ab/deterministic-codex-subagent-benchmark
Make NO changes to the repo (verify `git status` is clean when you finish). Put any scratch
files in a temp dir outside the repo.

Context you need: the repo is a benchmark that compares six Codex model/effort configurations
on one .NET async-cache repair task. Its purpose is to substantiate `skills/orchestrate/SKILL.md`,
a routing table that was produced by tuning and is published with this repo as its reference
output. Judge the claims against that purpose.

For each claim output exactly one of: CONFIRMED / REFUTED / PARTIALLY CORRECT / UNVERIFIABLE
(say why), with the evidence you used (file:line, command output, or experiment result).
Line numbers below are from the reviewed revision — if a number is off but the claim holds,
say so and give the right one.

## A. Mechanical claims — verify by reading code, not by inference

A1.  harness.py:440 skips `validate_run_id` for any run ID starting with "verify-", and the
     `evaluate` subcommand builds its default output path as RUNS/"results"/f"{run_id}.json"
     (~harness.py:1100). Therefore `--run-id 'verify-../../x'` writes outside runs/results.
     Check whether anything else constrains it. The JSON schema allows only `verify-[a-z0-9-]+`.
A2.  harness.py:~1164: run-job raises ValueError if any earlier same-machine job's result has
     status INFRA_FAILURE or INDETERMINATE_TIMEOUT. Confirm there is NO flag or env var to
     override this, i.e. one unresolved result blocks every later job on that machine.
A3.  harness.py:~908: aggregate raises unless --allow-incomplete when any run is missing or unresolved.
A4.  harness.py:~933-945: `eligible` excludes INFRA_FAILURE and INDETERMINATE_TIMEOUT, and
     passRate = successes / len(eligible). Confirm the denominator excludes them.
A5.  `read_capped` and `decoded_timeout_stream` (harness.py:~291-303) are called nowhere in
     harness.py — only from tests/test_harness.py. So two unit tests cover dead code.
A6.  schemas/run-result.schema.json is never parsed or used for validation anywhere; it is only
     sha256'd into provenance (harness.py:~232). No test validates a result against it.
A7.  `validate_result` (harness.py:~797-806) requires planHash, trial, orderPosition, and
     generationDurationSeconds. `evaluate_candidate` never sets those four. Therefore a result
     produced by the `evaluate` subcommand can never pass validate_result, so it can neither be
     aggregated nor satisfy a run-job predecessor check.
A8.  harness.py:~646-649: `path.is_symlink()` is checked AFTER `path.resolve()`, so that branch
     is unreachable.
A9.  `materialize` (harness.py:~167) and the hidden-tests copytree (~524) do not pass an `ignore`
     for bin/obj, while the candidate copytree (~488) does.
A10. fixtures/async-cache-v1/hidden/Cache.HiddenTests/Cache.HiddenTests.csproj has a fallback
     ItemGroup (condition: CandidateAssemblyPath == '') with ProjectReference "../Cache.Core/
     Cache.Core.csproj". Confirm that fixtures/async-cache-v1/hidden/Cache.Core/ does not exist,
     making that branch unbuildable.
A11. `ensure_under` (harness.py:~131-135) permits path == parent, and it guards an rmtree (~165).
A12. Secrets are written with the default umask then chmod'ed 0600 (generation log ~701,
     plan ~1110, result ~1190), whereas load_or_create_id_key (~154) uses O_EXCL with mode 0600.
A13. MAX_EVALUATOR_OUTPUT_BYTES is enforced per stream inside execute_captured's drain(), so a
     single run can retain up to 2x that in stdout+stderr combined.
A14. `fastMode` is hardcoded False (harness.py:~456) and is a required schema field; nothing varies it.
A15. Neither docker invocation (harness.py:~267 base_command, ~667 generate_candidate) passes
     `--user`, so containers run as root.
A16. .github/workflows/verify.yml triggers on workflow_dispatch only — the unit tests and the
     manifest check never run on push or PR.
A17. POLICY_PATTERNS (harness.py:~54-65) bans Task.Delay, GetAwaiter().GetResult(),
     ModuleInitializer, DllImport/LibraryImport, Process.Start, Assembly.Load*, and
     Environment.Exit/FailFast. Read fixtures/async-cache-v1/TASK.md and confirm which of those
     are actually disclosed to the agent. Also confirm a policy hit yields status
     CANDIDATE_FAILURE (~481), identical to a test failure, and that the aggregate `statuses`
     counts do not separate the two.
A18. "async-cache-v1" is hardcoded at harness.py:27,107,450,766,778,808 and as a `const` in
     schemas/run-result.schema.json:15 — so adding a second fixture requires touching all of them.
A19. No NuGet.config is materialized into any build workspace, and Directory.Build.props sets
     RestoreIgnoreFailedSources=true.
A20. AGENT_PROMPT (harness.py:~40-45) and TASK.md both instruct the agent NOT to spawn agents,
     and generate_candidate runs codex with --ignore-rules and --ignore-user-config (~679).
     Conclusion to check: the orchestrate skill is never loaded or exercised by the benchmark.
A21. `git log --all --diff-filter=A --name-only -- 'runs/**'` shows only .gitkeep files — no
     plan, results, summary, or verification report has EVER been committed on any branch.
A22. README.md:152 instructs keeping the repository private because it contains hidden-test
     source, while the repo ships an MIT LICENSE and is published.
A23. Result JSON files embed full stdout from the hidden suite, including hidden test names and
     assertion messages.
A24. The pinned Codex CLI (docker/package.json, @openai/codex 0.147.0) accepts every flag used in
     generate_candidate: --sandbox, --ephemeral, --ignore-user-config, --ignore-rules,
     --skip-git-repo-check, --json, --model, --config.
A25. NOT YET VERIFIED by the original review — please check: do the model slugs in matrix.json
     (gpt-5.6-luna, gpt-5.6-terra, gpt-5.6-sol) actually resolve in the installed Codex CLI?
     SKILL.md tells users to route work to those names, so if they don't resolve, the published
     skill is inoperable. Report what you find; do not spend money on a generation to find out.

## B. Experiments — reproduce these, don't take the reported outcome on faith

Baseline (expected: all pass; report timings):

    python3 harness.py manifest
    python3 -m unittest discover -s tests          # reported: 17 tests, OK
    python3 harness.py verify --backend native --repeat 3 --timeout 180 --output <tmp>/n.json
    python3 harness.py verify --backend docker --repeat 1 --timeout 300 --output <tmp>/d.json

B1. Mutant-to-hidden-test mapping. From the native verification JSON, for each mutant extract the
    FAIL lines from the last hidden-N attempt's stdout. The review claims this mapping, and claims
    that THREE hidden tests are covered by no mutant at all:
      - "different keys load concurrently"
      - "one cancelled waiter does not cancel shared load"
      - "invalidation supersedes an older in-flight generation"
    It also claims `invalidation-noop` is caught by the PUBLIC suite so it never reaches the hidden
    runner, and that `waiter-cancellation-evicts` trips "same-key misses are single flight" with a
    nondeterministic count ("Expected 1; got 18"). Verify all of this.

B2. Hang classification. Build a candidate that is correct in every respect EXCEPT that it wraps
    the entire body of GetAsync in a single process-wide SemaphoreSlim(1,1), so distinct keys
    serialize. Keep: ArgumentNullException on null timeProvider, ArgumentOutOfRangeException on
    TTL <= 0, factory receives CancellationToken.None, expiry computed after the factory completes,
    Invalidate = TryRemove. Then:

      harness.materialize(tmp); copy your file over Cache.Core/AsyncExpiringCache.cs
      harness.candidate_integrity(...) and harness.policy_violations(...)   # expected: both clean
      harness.evaluate_candidate(..., run_id="verify-serializing", repeat=1, timeout=15,
                                 backend="native", trusted=True)

    Claim: status is INDETERMINATE_TIMEOUT (not CANDIDATE_FAILURE), and the hidden runner's partial
    stdout shows it hung at "different keys load concurrently". Verify, and confirm the root cause:
    the hidden runner (hidden/Cache.HiddenTests/Program.cs) awaits bare TaskCompletionSources with
    no per-test timeout.

B3. Non-UTF-8 candidate. Write Cache.Core/AsyncExpiringCache.cs as UTF-16 bytes. Claim:
    candidate_integrity returns clean, then policy_violations raises an unhandled UnicodeDecodeError
    (harness.py:~221), so run-job would abort with a traceback and write no result file.

B4. NEGATIVE RESULT — the review suspected a bug here and then withdrew it. Re-test it, because if
    the withdrawal was wrong it matters: the generator container (harness.py:~667) sets neither
    DOTNET_CLI_HOME nor NUGET_PACKAGES, runs with --read-only rootfs and HOME=/root, yet TASK.md
    tells the agent to run `dotnet run --project Cache.PublicTests/Cache.PublicTests.csproj`.
    Reproduce the generator's flag set against the SDK-based image on a materialized workspace and
    confirm whether `dotnet run` succeeds. Reported outcome: it SUCCEEDS, so this is NOT a bug.

B5. Statistical power. Using harness.wilson, compute the 95% interval for n=36 (the README's
    example gives 36 generations per configuration) at p = 0.50/0.70/0.90, and check whether
    0.60 vs 0.85 separates at n=36. Reported: interval half-width ~0.15 at n=36, and 0.60 vs 0.85
    OVERLAPS; only about 0.55 vs 0.90 separates. Then judge: with six configurations (15 pairwise
    comparisons) and the multiplicity correction README:147 requires, can this design rank
    adjacent configurations at all?

B6. Build timing headroom. From the docker verification JSON, report the min/max duration of
    public-build and hidden-build. Reported: 3.0s to 13.0s on an idle machine against a 30s default
    timeout. Then confirm from harness.py:~500-501 that a FIRST attempt that times out and a SECOND
    that succeeds still yields INFRA_FAILURE (i.e. unresolved, which per A2 blocks the queue).

## C. Analytical claims — no command will settle these; argue them

C1. Because `eligible` excludes timeouts (A4) and deadlock is the characteristic failure of weak
    concurrency reasoning (B2), the harness censors the weakest configurations' most characteristic
    failures, and does so DIFFERENTIALLY — inflating pass rates most for the weakest configurations
    and flattening the ordering the routing table asserts. Is this reasoning sound? Is the direction
    of bias right?
C2. The harness reduces each artifact to PASS/CANDIDATE_FAILURE via a single marker count
    (harness.py:~566), but the hidden runner prints one PASS/FAIL line per behavior and the harness
    stores full stdout in the result file. Claim: an 8-dimensional behavior vector per generation is
    already recoverable from existing result files, would sharply increase discrimination per
    generation, and requires only post-processing — no re-generation. Assess.
C3. Claim: a single concurrency-repair fixture can support at most three of SKILL.md's six routing
    rows (luna-high's "isolated, well-specified, deterministic validation"; sol-high's concurrency
    claim; terra-medium's NEGATIVE claim about concurrency), and the positive claims for luna-low,
    luna-medium, terra-medium (exploration/triage/synthesis), and sol-medium are extrapolation to
    task shapes never run. Read SKILL.md and assess, row by row.
C4. Claim: given that SKILL.md is published tuned output and this repo is its reference, the repo's
    core deficiency is that it publishes a protocol with zero record (A21), and that its blinding
    machinery (HMAC run IDs keyed by a gitignored secret, gitignored 0600 plan) has no inverse —
    there is no command to emit a de-blinded public record once scoring is frozen. Assess, and say
    what minimum artifact set would actually substantiate the six routing rules.
C5. Claim: README.md is written prospectively ("Preregister ... before generation", README:145)
    for work that has already been done, so a reader cannot tell whether any campaign ran. Assess.

## D. Platform caveat — do not silently confirm

The original review ran on macOS (darwin), Docker 29.7.2, .NET SDK 10.0.301. It claimed that
because containers run as root (A15), bind-mounted build output (obj/, bin/, .nuget/, .dotnet-home/)
becomes root-owned on LINUX, so tempfile.TemporaryDirectory cleanup (harness.py:~485) and
materialize(replace=True)'s rmtree raise PermissionError — and that this would break the
`pinned-container` CI job on ubuntu-24.04. It explicitly could NOT reproduce this on macOS, because
Docker Desktop remaps ownership. Either verify it properly (run the docker-backend path from inside
a Linux container or a Linux VM and inspect ownership of the created files), or mark it
UNVERIFIABLE here. Do not mark it CONFIRMED on reasoning alone.

## Output

A table of claim ID -> verdict -> one-line evidence. Then, separately: (a) any claim you REFUTED,
with proof; (b) any material issue the review MISSED; (c) your own ranking of the top three
problems given that the repo's purpose is to substantiate a published routing table.

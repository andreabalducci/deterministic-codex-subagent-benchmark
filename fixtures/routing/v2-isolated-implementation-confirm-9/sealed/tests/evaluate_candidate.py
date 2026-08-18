#!/usr/bin/env python3
"""Execute sealed behavioral checks without writing to the candidate mount."""

import json
import ctypes
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile

sealed = pathlib.Path(__file__).resolve().parents[1]
spec = json.loads((sealed / "evaluator.json").read_text())
candidate_source = pathlib.Path(sys.argv[1]).resolve()
source_target = candidate_source / spec["target"]
if not source_target.is_file():
    raise SystemExit("missing target: " + spec["target"])


def _restrict_to_runtime_candidate_and_scratch(candidate: pathlib.Path, scratch: pathlib.Path):
    """Return a Linux pre-exec hook that denies evaluator-fixture filesystem reads."""
    if platform.system() != "Linux":
        return None

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathBeneath(ctypes.Structure):
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]

    execute, write_file, read_file, read_dir = 1, 2, 4, 8
    remove_dir, remove_file = 16, 32
    make = sum(1 << bit for bit in range(6, 13))
    handled = execute | write_file | read_file | read_dir | remove_dir | remove_file | make
    readonly = execute | read_file | read_dir
    writable = handled
    libc = ctypes.CDLL(None, use_errno=True)

    def apply():
        ruleset = Ruleset(handled)
        fd = libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
        opened = []
        try:
            for path, access in (
                (pathlib.Path("/usr"), readonly),
                (pathlib.Path("/lib"), readonly),
                (pathlib.Path("/lib64"), readonly),
                (pathlib.Path("/etc"), read_file | read_dir),
                (pathlib.Path("/dev"), read_file | read_dir | write_file),
                (pathlib.Path("/proc"), readonly),
                (pathlib.Path("/sys"), readonly),
                (candidate, readonly),
                (scratch, writable),
            ):
                if not path.exists():
                    continue
                parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
                opened.append(parent)
                rule = PathBeneath(access, parent)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) != 0:
                    raise OSError(ctypes.get_errno(), "landlock_add_rule")
            if libc.prctl(38, 1, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
            if libc.syscall(446, fd, 0) != 0:
                raise OSError(ctypes.get_errno(), "landlock_restrict_self")
        finally:
            for opened_fd in opened:
                os.close(opened_fd)
            os.close(fd)

    return apply


def run(command, cwd, *, input_text=None, restricted=False):
    preexec = _restrict_to_runtime_candidate_and_scratch(candidate, pathlib.Path(cwd)) \
        if restricted else None
    attempt = subprocess.run(
        command, cwd=cwd, text=True, input=input_text, capture_output=True,
        preexec_fn=preexec,
    )
    if attempt.returncode:
        sys.stdout.write(attempt.stdout)
        sys.stderr.write(attempt.stderr)
        raise SystemExit(attempt.returncode)


kind = spec["testKind"]
with tempfile.TemporaryDirectory(prefix="routing-evaluator-") as temporary:
    scratch = pathlib.Path(temporary)
    candidate = scratch / "candidate"
    shutil.copytree(candidate_source, candidate)
    target = candidate / spec["target"]

    if kind == "dotnet-behavior":
        os.environ["HOME"] = str(scratch)
        os.environ["DOTNET_CLI_HOME"] = str(scratch / ".dotnet")
        os.environ["NUGET_PACKAGES"] = str(scratch / ".nuget" / "packages")
        tests = scratch / "tests"
        shutil.copytree(sealed / "tests", tests)
        copied_target = scratch / "Candidate.cs"
        shutil.copy2(target, copied_target)
        run([
            "dotnet", "build", str(tests / "Evaluator.csproj"),
            "--property:CandidateFile=" + str(copied_target),
            "--property:RestoreIgnoreFailedSources=true",
        ], scratch)
        # The restricted child receives only compiled artifacts, never sealed source.
        (tests / "Program.cs").unlink()
        (tests / "Evaluator.csproj").unlink()
        copied_target.unlink()
        run([
            "dotnet", str(tests / "bin" / "Debug" / "net10.0" / "Evaluator.dll")
        ], scratch, restricted=True)
    elif kind == "node-behavior":
        behavior = (sealed / "tests" / "evaluate.ts").read_text()
        wrapper = (
            "import{stripTypeScriptTypes}from'node:module';"
            "const c=[];for await(const x of process.stdin)c.push(x);"
            "process.argv.splice(1,0,'-');"
            "const s=stripTypeScriptTypes(c.join(''),{mode:'strip'});"
            "await import('data:text/javascript;base64,'+Buffer.from(s).toString('base64'));"
        )
        run([
            "node", "--no-warnings=ExperimentalWarning", "--input-type=module",
            "--eval", wrapper, str(target),
        ], scratch, input_text=behavior, restricted=True)
    elif kind == "python-behavior":
        behavior = (sealed / "tests" / "behavior.py").read_text()
        run(
            [sys.executable, "-", str(target)], scratch,
            input_text=behavior, restricted=True,
        )
    elif kind == "json-contract":
        actual = json.loads(target.read_text())
        for case in spec["cases"]:
            cursor = actual
            for part in case["path"]:
                cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
            if cursor != case["expected"]:
                raise AssertionError(case["name"])
    elif kind == "sqlite-migration":
        import sqlite3

        db = sqlite3.connect(":memory:")
        def authorize(action, argument1, argument2, _database, _source):
            if action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION and "load_extension" in {argument1, argument2}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        db.executescript(spec["setupSql"])
        db.executescript(target.read_text())
        for case in spec["cases"]:
            try:
                db.execute(case["statement"], case.get("params", []))
                db.commit()
            except sqlite3.IntegrityError:
                if not case.get("reject"):
                    raise
            else:
                if case.get("reject"):
                    raise AssertionError(case["name"] + " was accepted")
    else:
        raise AssertionError("unknown test kind: " + kind)

print(json.dumps({"status": "PASS", "target": spec["target"], "cases": len(spec.get("cases", []))}))

from __future__ import annotations
import json
import pathlib
import sys

fixture = pathlib.Path(__file__).resolve().parent.parent
candidate = pathlib.Path(sys.argv[1]).resolve()
spec = json.loads((fixture / "sealed" / "expected.json").read_text(encoding="utf-8"))
source = json.loads((fixture / "starter" / spec["relative"]).read_text(encoding="utf-8"))
actual = json.loads((candidate / spec["relative"]).read_text(encoding="utf-8"))
expected = dict(source)
expected.update(spec["additions"])
if actual != expected:
    raise SystemExit("mapping differs from the one bounded addition or mutates existing entries")

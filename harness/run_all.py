# SPDX-License-Identifier: MIT
"""Run the dry-run suites, record ledgers/runs.json (+ mutants.json), exit non-zero on any failure.
Usage: python -m harness.run_all [--no-record]"""
import importlib
import io
import json
import os
import subprocess
import sys

from . import ledger


def commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ledger.ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "uncommitted"


def main():
    record = "--no-record" not in sys.argv
    suites = json.load(io.open(os.path.join(ledger.ROOT, "harness", "suites.json"), encoding="utf-8"))
    bad = 0
    for name, s in suites.items():
        if not s.get("ci"):
            print("skip  %-10s (%s)" % (name, s["about"]))
            continue
        rows = importlib.import_module(s["module"]).run()
        if name == "mutants":
            fails = [r for r in rows if r["verdict"] != "killed"]
            if record:
                ledger.record_mutants("privacy", rows)
        else:
            fails = [r for r in rows if not r["pass"]]
        for r in fails:
            print("  FAIL %s: %s" % (r["name"], r.get("detail", "")))
        print("%s  %-10s %d/%d" % ("ok  " if not fails else "FAIL", name, len(rows) - len(fails), len(rows)))
        if record:
            ledger.record("runs", name, {"total": len(rows), "passed": len(rows) - len(fails), "commit": commit()})
            if name == "privacy":
                ledger.record("privacy", "deny_paths", {"cases": [{"name": r["name"], "pass": r["pass"]} for r in rows]})
        bad += len(fails)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

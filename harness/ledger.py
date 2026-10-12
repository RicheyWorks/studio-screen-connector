# SPDX-License-Identifier: MIT
"""The harness's ledgers: JSON files under ledgers/, merged per key so a run updates only its own entry.
Copied from RicheyWorks/claude-unreal-connector harness/ledger.py (itself ported from CSRBT's tools/mutant_ledger.py)."""
import io
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "ledgers")


def path(name):
    return os.path.join(DIR, name + ".json")


def load(name):
    p = path(name)
    if not os.path.isfile(p):
        return {}
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def record(name, key, entry):
    """Merge one entry (stamped with `at`) under `key` in ledgers/<name>.json, keeping the others."""
    os.makedirs(DIR, exist_ok=True)
    led = load(name)
    entry = dict(entry, at=int(time.time()))
    led.setdefault("entries", {})[key] = entry
    with io.open(path(name), "w", encoding="utf-8") as f:
        json.dump(led, f, indent=1, sort_keys=True)
    return entry


def record_mutants(runner, rows, equivalent=()):
    killed = sum(1 for r in rows if r["verdict"] == "killed")
    survived = sum(1 for r in rows if r["verdict"] == "SURVIVED")
    return record("mutants", runner, {
        "mutants": len(rows), "killed": killed, "survived": survived,
        "inconclusive": len(rows) - killed - survived, "equivalent": len(equivalent),
        "rows": [{"name": r["name"], "verdict": r["verdict"], "detail": (r.get("detail") or "")[:120]} for r in rows],
        "equivalents": [{"name": n, "why": w} for n, w in equivalent]})

#!/usr/bin/env python3
"""
Validate this pack against the JSON Schemas that ship inside a real Cribl Stream
install, so schema violations are caught here instead of at "Add Pack" time.

    python3 tools/validate-schema.py            # uses $CRIBL_HOME or /opt/cribl

Why this exists: Cribl validates a Pack with AJV on install and reports only the
keyword that failed - e.g. "failed to install: should be equal to one of the
allowed values" - with no field path. That is unusable for debugging. The same
schemas are on disk in any Cribl install under:

    $CRIBL_HOME/default/cribl/collectors/<type>/conf.schema.json
    $CRIBL_HOME/default/cribl/functions/<id>/conf.schema.json

so we validate against those and report the exact JSON pointer instead.

This is a best-effort check, not a complete one: Cribl keeps the schemas for
pack.yml, vars.yml, breakers.yml and the job wrapper (schedule/input) inside its
bundled JS, not on disk. Collector conf and Function conf are where the enums
live, so this covers the failure modes that actually bite.

Exit codes: 0 = clean or skipped, 1 = schema violation(s).
"""
import glob
import json
import os
import sys

CRIBL_HOME = os.environ.get("CRIBL_HOME", "/opt/cribl")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def skip(reason):
    print(f"  schema validation skipped: {reason}")
    sys.exit(0)


try:
    import yaml
except ImportError:
    skip("pyyaml not installed")

try:
    from jsonschema import Draft7Validator
except ImportError:
    skip("jsonschema not installed (python3 -m pip install --user jsonschema)")

SCHEMA_ROOT = os.path.join(CRIBL_HOME, "default", "cribl")
if not os.path.isdir(SCHEMA_ROOT):
    skip(f"no Cribl install at {CRIBL_HOME} (set CRIBL_HOME to one)")

_cache = {}


def validator_for(kind, name):
    """kind is 'collectors' or 'functions'. Returns a validator, or None."""
    key = (kind, name)
    if key not in _cache:
        path = os.path.join(SCHEMA_ROOT, kind, name, "conf.schema.json")
        _cache[key] = (
            Draft7Validator(json.load(open(path))) if os.path.exists(path) else None
        )
    return _cache[key]


def report(where, errors):
    for e in errors:
        pointer = "/".join(str(p) for p in e.absolute_path) or "<root>"
        print(f"error: {where}: {pointer}: {e.validator}: {e.message}", file=sys.stderr)
        # oneOf/anyOf failures are useless without the branch errors.
        for sub in e.context or []:
            sub_pointer = "/".join(str(p) for p in sub.absolute_path) or "<root>"
            print(f"       -> {sub_pointer}: {sub.validator}: {sub.message}", file=sys.stderr)


bad = 0
checked = 0

# --- Collectors (default/jobs.yml) ----------------------------------------
jobs_path = os.path.join(ROOT, "default", "jobs.yml")
if os.path.exists(jobs_path):
    for job_id, job in (yaml.safe_load(open(jobs_path)) or {}).items():
        collector = (job or {}).get("collector") or {}
        ctype = collector.get("type")
        if not ctype:
            continue
        v = validator_for("collectors", ctype)
        if v is None:
            print(f"error: {job_id}: unknown collector type '{ctype}'", file=sys.stderr)
            bad += 1
            continue
        errors = sorted(v.iter_errors(collector.get("conf") or {}),
                        key=lambda e: list(e.absolute_path))
        checked += 1
        if errors:
            report(f"default/jobs.yml:{job_id}", errors)
            bad += len(errors)

# --- Pipeline functions ---------------------------------------------------
for path in sorted(glob.glob(os.path.join(ROOT, "default", "pipelines", "*", "conf.yml"))):
    rel = os.path.relpath(path, ROOT)
    for i, fn in enumerate((yaml.safe_load(open(path)) or {}).get("functions") or []):
        fid = fn.get("id")
        v = validator_for("functions", fid)
        if v is None:
            print(f"error: {rel}: functions[{i}]: no such Function id '{fid}'", file=sys.stderr)
            bad += 1
            continue
        errors = sorted(v.iter_errors(fn.get("conf") or {}),
                        key=lambda e: list(e.absolute_path))
        checked += 1
        if errors:
            report(f"{rel}:functions[{i}] ({fid})", errors)
            bad += len(errors)

if bad:
    print(f"\n{bad} schema violation(s) - Cribl would reject this pack on install",
          file=sys.stderr)
    sys.exit(1)

try:
    cribl_version = json.load(open(os.path.join(CRIBL_HOME, "package.json")))["version"]
except Exception:
    cribl_version = "unknown"
print(f"  schema ok ({checked} collector/function confs vs Cribl {cribl_version})")

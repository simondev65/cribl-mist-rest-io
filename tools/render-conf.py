#!/usr/bin/env python3
"""Render the pack's YAML into paste-ready JSON under conf/ (gitignored).

    python3 tools/render-conf.py

default/*.yml is the source of truth and the thing that ships. conf/ exists only
so you can paste a single object into the Cribl UI's "Configure as JSON" tab and
test it against a live Mist org without installing the pack. It is regenerated
from default/, so the two cannot drift.

Outputs:
    conf/collectors/<collector-id>.json    Data > Sources > Collectors > REST/API
    conf/breakers/<ruleset>.json           Processing > Knowledge > Event Breaker Rules
    conf/pipelines/<pipeline-id>.json      Processing > Pipelines
    conf/routes.json                       Routing > Data Routes
    conf/vars.json                         Processing > Knowledge > Variables
    conf/README.md                         paste instructions
"""

import json
import os
import re
import shutil
import sys

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required: python3 -m pip install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT = os.path.join(ROOT, "default")
CONF = os.path.join(ROOT, "conf")


def load(rel):
    path = os.path.join(DEFAULT, rel)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return yaml.safe_load(fh)


def dump(rel, obj):
    path = os.path.join(CONF, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(json.dumps(obj, indent=2) + "\n")
    return path


README = """# conf/ — paste-ready JSON (generated, gitignored)

Generated from `default/` by `python3 tools/render-conf.py`. **Do not edit these
files** — edit the YAML under `default/` and re-run the script. Nothing here is
committed; `default/` is the pack.

Use this when you want to smoke-test a collector against a live Mist org without
installing the pack.

## Order matters

Build the dependencies first, or the collector will save with dangling
references.

### 1. Secret — `mist_api_token`

**Settings → Security → Secrets → Add Secret → Text**

| Field | Value |
|---|---|
| Secret name | `mist_api_token` |
| Value | your Mist API token |

Create the token in the Mist portal under your account menu → **My Account →
API Tokens**, or `POST /api/v1/self/apitokens`. Give it read access to the org.
The pack sends it as `Authorization: Token <token>` — never as `Bearer`.

### 2. Variables — `conf/vars.json`

**Processing → Knowledge → Variables**

Add each variable from `conf/vars.json` by hand (the Variables UI has no bulk
JSON import). At minimum set `mist_api_host` and `mist_org_id`. Skipping this
step makes every `collectUrl` resolve to
`https://undefined/api/v1/orgs/undefined/...`.

### 3. Event Breaker ruleset — `conf/breakers/*.json`

**Processing → Knowledge → Event Breaker Rules → Add Ruleset → Configure as
JSON** — paste the file contents, Save.

### 4. Pipelines — `conf/pipelines/*.json`

**Processing → Pipelines → Add Pipeline → Configure as JSON** — paste, Save.
Create `cribl_mist_parse` first; the collectors reference it by name.

### 5. Collectors — `conf/collectors/*.json`

**Data → Sources → Collectors → REST/API → Add Collector → Configure as JSON**
— paste, Save.

Every collector ships with `schedule.enabled: false`. Use **Run → Preview** on
one collector first: it fires a single request and shows you the broken-out
events without writing anything downstream. Only then enable the schedule.

## Dry-run each collector by hand first

Cheapest possible check that the token and org id are right:

```bash
MIST_HOST=api.mist.com
MIST_ORG=<your-org-uuid>
MIST_TOKEN=<your-token>

# Should return your admin identity and the orgs the token can see
curl -sS -H "Authorization: Token $MIST_TOKEN" \\
  "https://$MIST_HOST/api/v1/self" | jq '{email, privileges: [.privileges[].scope]}'

# Should return {start, end, limit, total, next, results:[...]}
curl -sS -H "Authorization: Token $MIST_TOKEN" \\
  "https://$MIST_HOST/api/v1/orgs/$MIST_ORG/logs?limit=5" | jq 'keys'
```

A `401` means the token is wrong or missing the `Token ` prefix. A `403` means
the token is valid but lacks org scope. A `429` means you are over the
5000 calls/hour budget — back off before enabling schedules.

## Caveats when testing outside the pack

- `conf/routes.json` uses `output: __group` / `targetContext: group`, which only
  means something inside a pack. Pasting it at Worker Group level will not do
  what you want — test the pipelines directly against `data/samples/` instead.
- `input.pipeline` and `input.breakerRulesets` reference the pack's pipeline and
  ruleset **by name**. Steps 3 and 4 above create objects with those exact
  names at group level, so the references resolve.
"""


def main():
    if os.path.isdir(CONF):
        shutil.rmtree(CONF)
    os.makedirs(CONF)

    written = []

    jobs = load("jobs.yml") or {}
    for cid, body in jobs.items():
        obj = {"id": cid}
        obj.update(body)
        written.append(dump(os.path.join("collectors", cid + ".json"), obj))

    breakers = load("breakers.yml") or {}
    for name, body in breakers.items():
        obj = {"id": name, "lib": body.get("lib", "custom")}
        obj.update({k: v for k, v in body.items() if k != "lib"})
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        written.append(dump(os.path.join("breakers", slug + ".json"), obj))

    pipe_root = os.path.join(DEFAULT, "pipelines")
    for entry in sorted(os.listdir(pipe_root)):
        conf = os.path.join(pipe_root, entry, "conf.yml")
        if not os.path.isfile(conf):
            continue
        with open(conf) as fh:
            body = yaml.safe_load(fh)
        obj = {"id": entry, "conf": body}
        written.append(dump(os.path.join("pipelines", entry + ".json"), obj))

    routes = load("pipelines/route.yml")
    if routes:
        written.append(dump("routes.json", routes))

    vars_ = load("vars.yml")
    if vars_:
        written.append(dump("vars.json", vars_))

    with open(os.path.join(CONF, "README.md"), "w") as fh:
        fh.write(README)
    written.append(os.path.join(CONF, "README.md"))

    for p in written:
        print("  " + os.path.relpath(p, ROOT))
    print(f"\n{len(written)} files written to conf/ (gitignored)")


if __name__ == "__main__":
    main()

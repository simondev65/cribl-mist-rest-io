# Juniper Mist REST Collector IO

A Cribl Stream Pack that collects Juniper Mist Cloud data over the Mist REST API
and emits it either as enriched JSON or as OCSF 1.4.

Eight REST Collectors, one Event Breaker ruleset, three Pipelines and a Route
table. Everything is parameterised through Pack Variables, so the only per-org
setup is a token, an org id and your regional cloud hostname.

---

## 1. Overview

Mist has no syslog egress worth collecting — everything meaningful lives behind
`https://api.<cloud>.mist.com/api/v1/`. This pack does that collection properly:

- **Org-level endpoints wherever they exist.** Mist exposes
  `/orgs/<org>/alarms/search`, `/orgs/<org>/devices/events/search` and friends,
  which already cover every site in one request. Fanning out per site would burn
  one API call per site per run against a 5000 calls/hour token budget for the
  same data.
- **Site fan-out only where it is unavoidable.** Rogue AP detections
  (`/sites/<site>/rogues/events/search`) have no org-level equivalent, so that
  one collector uses HTTP Discovery against `/orgs/<org>/sites`. It doubles as
  the template for any other site-scoped Mist endpoint you want to add.
- **Correct pagination per endpoint.** Mist's `/search` endpoints do *not* accept
  a `page` parameter — they return a `next` URL carrying a `search_after` cursor.
  The list endpoints (`/logs`, `/stats/devices`, `/sites`) do accept `page`. The
  pack uses `response_body` for the former and `request_page` (the UI's
  **Page/Size**) for the latter.
- **State tracking on the event streams.** Each event collector remembers the
  latest `_time` it saw and starts the next run from there, so a missed or
  skipped run backfills instead of leaving a hole.
- **API errors stay visible.** A `401` from a rotated token or a `429` from
  over-collection becomes a searchable `mist:api_error` event rather than
  silently looking like "no data".

### Data flow

```
  Mist Cloud REST API
        |
        v
  in_mist_*  (8 REST Collectors, jobs.yml)
        |  breakerRulesets: "Juniper Mist API Ruleset"
        v
  cribl_mist_parse           _time, sourcetype, vendor/product, host, org/site
        |
        v
  Routes (route.yml)         mist_output_format == 'ocsf' ?
        |                         |
        |                         +--> cribl_mist_ocsf     OCSF 1.4 in _raw
        +-----------------------------> cribl_mist_events  enriched Mist JSON
                                            |
                                            v
                                   your Worker Group Destinations
```

---

## 2. Prerequisites

| Requirement | Detail |
|---|---|
| Cribl Stream | 4.17.0 or later (Pack format 4.0+) |
| Mist API token | Org-scoped read token, stored as a Cribl **text secret** named `mist_api_token` |
| Mist org id | The Organization UUID |
| Mist cloud hostname | e.g. `api.mist.com`, `api.eu.mist.com` — see the table below |
| Network | Outbound HTTPS/443 from the Worker Group to your Mist cloud |

### Creating the API token

In the Mist portal: your account menu → **My Account → API Tokens → Create
Token**. Or `POST /api/v1/self/apitokens`. Grant it read access to the org you
want to collect; the pack issues only `GET` requests.

### Storing the token in Cribl

**Settings → Security → Secrets → Add Secret → Text**

| Field | Value |
|---|---|
| Secret name | `mist_api_token` |
| Value | the Mist API token |

The collectors reference it as `` `Token ${C.Secret('mist_api_token').value}` ``.
Mist uses the `Token` scheme, **not** `Bearer`. The token never appears in the
pack — secrets are stripped on export, and nothing in this repo contains one.

### Regional clouds

Set `mist_api_host` to match the cloud your org lives in. Getting this wrong
produces `403`s or an empty org.

| Region | Host | Region | Host |
|---|---|---|---|
| Global 01 | `api.mist.com` | EMEA 01 | `api.eu.mist.com` |
| Global 02 | `api.gc1.mist.com` | EMEA 02 | `api.gc3.mist.com` |
| Global 03 | `api.ac2.mist.com` | EMEA 03 | `api.ac6.mist.com` |
| Global 04 | `api.gc2.mist.com` | EMEA 04 | `api.gc6.mist.com` |
| Global 05 | `api.gc4.mist.com` | APAC 01 | `api.ac5.mist.com` |
| | | APAC 02 | `api.gc5.mist.com` |
| | | APAC 03 | `api.gc7.mist.com` |

---

## 3. What's included

### Collectors — `default/jobs.yml`

All eight ship **disabled** (`schedule.enabled: false`). Enable only the datasets
you need; each one you turn on spends part of the same hourly API budget.

| Collector | Endpoint | Pagination | Discovery | State tracking | Cron |
|---|---|---|---|---|---|
| `in_mist_org_audit_logs` | `/api/v1/orgs/<org>/logs` | `request_page` | none | yes | `*/5 * * * *` |
| `in_mist_org_alarms` | `/api/v1/orgs/<org>/alarms/search` | `response_body` | none | yes | `*/5 * * * *` |
| `in_mist_org_device_events` | `/api/v1/orgs/<org>/devices/events/search` | `response_body` | none | yes | `*/5 * * * *` |
| `in_mist_org_client_events` | `/api/v1/orgs/<org>/clients/events/search` | `response_body` | none | yes | `*/5 * * * *` |
| `in_mist_org_nac_client_events` | `/api/v1/orgs/<org>/nac_clients/events/search` | `response_body` | none | yes | `*/5 * * * *` |
| `in_mist_org_device_inventory` | `/api/v1/orgs/<org>/devices/search` | `response_body` | list | no | `17 * * * *` |
| `in_mist_org_device_stats` | `/api/v1/orgs/<org>/stats/devices` | `request_page` | none | no | `*/15 * * * *` |
| `in_mist_site_rogue_events` | `/api/v1/sites/<site>/rogues/events/search` | `response_body` | http | no | `*/15 * * * *` |

Each collector stamps two metadata fields the rest of the pack routes on:
`__packsource = 'cribl-mist-rest-io.mist-api'` and `mist_dataset = <name>`.

Three discovery paradigms are represented on purpose, so the pack works as a
worked example:

- **none** — a single org-level endpoint (six of the eight).
- **list** (`in_mist_org_device_inventory`) — `/orgs/<org>/devices/search` takes
  a `type` parameter whose enum is `ap | switch | gateway` with **no `all`
  value**, so Item List discovery issues one request per device type and stamps
  `mist_device_type` from `${id}`.
- **http** (`in_mist_site_rogue_events`) — enumerate `/orgs/<org>/sites`, then
  one collect task per site with `${id}` in the URL, stamping `mist_site_id` and
  `mist_site_name` from `${__collectible.*}`.

### Event Breaker — `default/breakers.yml`

One ruleset, `Juniper Mist API Ruleset`, three rules in order:

| Rule | Condition | Type | Behaviour |
|---|---|---|---|
| Mist API Error | body starts with `{"detail":` | `regex` (`/^\b$/`) | whole payload as one event, tagged `mist_api_error: true` |
| Mist Search Envelope | body contains `"results"` | `json_array` on `results` | one event per record, `_time` from `timestamp` |
| Mist Bare Array | fallback | `json_array` at root | one event per element, `_time` from `last_seen` |

`/orgs/<org>/stats/devices` and `/orgs/<org>/sites` return a top-level array with
no envelope, which is why the third rule leaves `jsonArrayField` unset.

### Pipelines — `default/pipelines/`

| Pipeline | Role |
|---|---|
| `cribl_mist_parse` | Pre-processing on every collector. `_time` from `timestamp`/`last_seen`/`modified_time`/`created_time`; `sourcetype = mist:<dataset>`; `vendor`/`product`; best-effort `host`; `mist_org_id`/`mist_site_id`; retags API error bodies as `mist_dataset = 'api_error'`. |
| `cribl_mist_events` | Default output. Serializes the enriched record to JSON in `_raw`, applies `index`, then strips everything else. |
| `cribl_mist_ocsf` | OCSF 1.4. Builds a `__ocsf` scratch object through chained Evals and `JSON.stringify`s it into `_raw`. |

### OCSF 1.4 coverage — `cribl_mist_ocsf`

| Mist dataset | OCSF class | Notes |
|---|---|---|
| `audit_logs` | **6003** API Activity | `activity_id` inferred from the message text (create/read/update/delete), defaulting to `99` Other rather than guessing |
| `alarms` | **2004** Detection Finding | `severity_id` from Mist `severity`; `status_id` from `status`/`acked`; `finding_info.src_url` deep-links to the Mist portal |
| `rogue_events` | **2004** Detection Finding | fixed `severity_id: 3` — see Limitations |
| `nac_client_events` | **3002** Authentication | `status_id` and `severity_id` from the `NAC_CLIENT_*` event type; `auth_protocol` from `auth_type` |
| `device_events`, `client_events`, `device_inventory`, `device_stats`, `api_error` | *(none)* | route to `cribl_mist_events` as enriched JSON |

Everything Mist sends that has no OCSF home lands under `unmapped`, so nothing is
lost in translation.

### Routes — `default/pipelines/route.yml`

| Route | Filter | Pipeline |
|---|---|---|
| `mist_ocsf` | pack source **and** `mist_output_format == 'ocsf'` **and** dataset has an OCSF class | `cribl_mist_ocsf` |
| `mist_json` | pack source | `cribl_mist_events` |
| `default` | `true` | *(none — pass-through)* |

Both Mist routes are `final`, so exactly one fires per event.

### Samples — `data/samples/`

Nine sample files, generated from the community Mist OpenAPI spec so the field
names in the pipelines are not guesswork. They are stored **post-breaker** (one
event per Mist record, fields flattened, plus `_raw` and the collector metadata),
which makes Data Preview behave exactly like a live run.

---

## 4. Configuration

**Pack Settings → Variables**, or `default/vars.yml`.

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `mist_api_host` | `api.mist.com` | **yes** | Your regional Mist cloud hostname, no scheme, no trailing slash |
| `mist_org_id` | `YOUR_MIST_ORG_ID` | **yes** | Mist Organization UUID |
| `mist_page_limit` | `1000` | no | Records per request on the six `/search`-style collectors. Mist caps most endpoints at 1000; lower it if you see `400`s or timeouts. Does **not** apply to `in_mist_org_audit_logs` or `in_mist_org_device_stats` — Cribl's Page/Size pagination fields are static numbers, so those two carry the record limit in **Pagination → Record limit** on the collector instead |
| `mist_lookback_seconds` | `1200` | no | Collection window for `in_mist_site_rogue_events`, which has no state tracking. Keep it larger than that collector's cron interval |
| `mist_output_format` | `json` | no | `json` or `ocsf` |
| `mist_splunk_index` | `juniper_mist` | no | Value written to `index`. Set to `''` to leave `index` unset (S3 / Lake / HTTP destinations) |

You need the secret too — see [Prerequisites](#2-prerequisites). It is a Cribl
secret, not a Variable, so it is never serialised into the pack.

### API request budget

A Mist API token is limited to **5000 calls per hour**, and that budget is shared
by everything using the token. Rough cost per run of each collector:

```
org-level event collectors    1 call + 1 per extra page
device inventory              3 calls (one per device type) + pages
device stats                  1 call + 1 per 100 devices
site rogue events             1 discovery call + 1 call per site + pages
```

Five org-level collectors on `*/5` is 12 runs/hour each — about 60 calls/hour
before pagination. The site fan-out is the one to watch: 200 sites on `*/15`
is 4 × 201 ≈ 800 calls/hour. Every collector retries `429` with exponential
backoff honouring `retry-after`, but staying inside the budget beats retrying.

---

## 5. Usage

1. **Install the pack** — *Processing → Packs → Add Pack*, then upload the
   `.crbl` or point Cribl at this Git repo.
2. **Create the `mist_api_token` text secret** (see Prerequisites).
3. **Set `mist_api_host` and `mist_org_id`** in Pack Settings → Variables.
4. **Preview one collector.** Open `in_mist_org_audit_logs` → **Run → Preview**.
   That fires a single request and shows the broken-out events without writing
   anything downstream. Confirm you see one event per audit entry, not one event
   per HTTP response.
5. **Enable the collectors you want.** Set `schedule.enabled: true` on each.
6. **Wire the output.** The pack's routes use `output: __group`, so events return
   to the Worker Group's own Routes and Destinations. Filter on
   `__packsource == 'cribl-mist-rest-io.mist-api'` at group level, or on
   `sourcetype` (`mist:*` / `ocsf:*`).
7. **Switch to OCSF** by setting `mist_output_format` to `ocsf` whenever you are
   ready. The four mappable datasets flip; everything else keeps emitting JSON.

### Testing without installing the pack

`conf/` holds paste-ready JSON for each collector, breaker, pipeline and the
variable list — generated from `default/`, gitignored, never committed:

```bash
python3 tools/render-conf.py     # writes conf/, see conf/README.md for paste order
```

Use it when you want to smoke-test one collector against a live org from the
Cribl UI's *Configure as JSON* tabs. `default/` remains the only source of truth;
regenerate rather than editing `conf/` by hand.

### Offline dry run

`tools/simulate.js` pushes every sample through `cribl_mist_parse`, the route
table and the matching output pipeline, then asserts the result: `_raw` parses as
JSON, `_time` is a plausible epoch, no internal fields leaked past the whitelist,
and for OCSF that the required attributes are present and
`type_uid == class_uid * 100 + activity_id`.

```bash
node tools/simulate.js          # mist_output_format = json
node tools/simulate.js ocsf     # force OCSF
```

It is a small stand-in for Cribl's expression engine, not an emulator — it
catches broken field names and malformed OCSF before you touch a Worker Group,
but Data Preview is still the source of truth.

---

## 6. Validation

### Audit log → OCSF 6003 API Activity

Input (one element of `results` from `/orgs/<org>/logs`):

```json
{
  "admin_id": "72bfa2bd-e58a-4670-9d20-a1468f7a6f58",
  "admin_name": "test@mistsys.com",
  "id": "c6f9347b-b0a4-4a23-b927-fa9249f2ffb2",
  "message": "TEST AUDIT",
  "org_id": "2818e386-8dec-2562-9ede-5b8a0fbbdc71",
  "site_id": "4ac1dcf4-9d8b-7211-65c4-057819f0862b",
  "timestamp": 1431382121
}
```

Output `_raw` with `mist_output_format = 'ocsf'` (abridged):

```json
{
  "time": 1431382121000,
  "severity_id": 1, "severity": "Informational",
  "category_uid": 6, "category_name": "Application Activity",
  "class_uid": 6003, "class_name": "API Activity",
  "activity_id": 99, "activity_name": "Other",
  "type_uid": 600399,
  "status_id": 1, "status": "Success",
  "message": "TEST AUDIT",
  "api": { "operation": "TEST AUDIT", "service": { "name": "Juniper Mist Cloud API" } },
  "actor": { "user": { "name": "test@mistsys.com", "uid": "72bfa2bd-...", "type_id": 2, "type": "Admin" } },
  "metadata": {
    "version": "1.4.0", "profiles": ["cloud"],
    "uid": "c6f9347b-b0a4-4a23-b927-fa9249f2ffb2",
    "log_name": "audit_logs", "log_provider": "Juniper Mist",
    "product": { "vendor_name": "Juniper", "name": "Mist", "feature": { "name": "audit_logs" } }
  },
  "unmapped": { "org_id": "2818e386-...", "site_id": "4ac1dcf4-...", "mist_log_id": "c6f9347b-..." }
}
```

Top-level fields: `_time=1431382121`, `sourcetype=ocsf:api_activity`,
`host=test@mistsys.com`, `class_uid=6003`, `index=juniper_mist`.

### NAC event → OCSF 3002 Authentication

`{"type": "NAC_CLIENT_PERMIT", "username": "user@deaflyz.net", "mac": "ac3eb179e535",
"auth_type": "eap-ttls", "ssid": "mist_nac", "vlan": "750", ...}` becomes
`class_uid 3002`, `activity_id 1` (Logon), `type_uid 300201`, `status_id 1`
(Success), `auth_protocol "eap-ttls"`, `user.name "user@deaflyz.net"`,
`src_endpoint.mac "ac3eb179e535"`, `src_endpoint.vlan_uid "750"`, with the NAC
rule ids, IDP roles and RADIUS AVPs under `unmapped`.

A `NAC_CLIENT_DENY` on the same shape yields `status_id 2` (Failure) and
`severity_id 3` (Medium).

### Reproduce both

```bash
node tools/simulate.js ocsf
```

---

## 7. Limitations & Known Issues

- **Rogue events carry no rogue class.** A Mist rogue event is only
  `{ap, bssid, ssid, channel, rssi, timestamp}`. The class
  (`honeypot | lan | others | spoof`) is a *request* filter that Mist does not
  echo back, so `cribl_mist_ocsf` cannot derive severity from the record and
  defaults to `3` (Medium). For per-class severity, clone
  `in_mist_site_rogue_events`, add `type=<class>` to `collectRequestParams`, and
  add a matching `mist_rogue_type` entry under `input.metadata` — the OCSF
  pipeline already reads that field when present.
- **`in_mist_site_rogue_events` has no state tracking**, because its `${id}`
  fan-out means state would have to be keyed per site. It uses a fixed
  `mist_lookback_seconds` window instead, which is deliberately wider than its
  cron interval. Expect a small number of duplicate events at the window
  boundary; dedupe on `bssid` + `timestamp` downstream if that matters.
- **NAC events may paginate only one page deep.** The Mist OpenAPI spec does not
  declare a `next` attribute on `/orgs/<org>/nac_clients/events/search`, unlike
  every other `/search` endpoint. With `lastPageExpr: "!next"` the collector
  stops after page one if Mist omits it, capping a run at `mist_page_limit`
  records. On a `*/5` schedule with `limit=1000` that is 12000 events/hour of
  headroom — raise the frequency or narrow the collection with a `type` filter if
  your org exceeds it.
- **Relative `next` URLs.** Mist returns `next` as a path
  (`/api/v1/orgs/<org>/alarms/search?...&search_after=...`), not an absolute URL,
  and the `start`/`end`/`limit` parameters already on the request are re-appended
  to it. The values are identical either way, so first-wins and last-wins both
  produce the correct window — but confirm page 2 arrives in Preview on your
  first run.
- **`device_stats` records are large** (10 KB+ each, with nested `ble_stat`,
  `lldp_stats`, `if_stat`). At org scale this is the most expensive dataset by
  volume. Use the endpoint's `fields` parameter to trim it if you only need a few
  metrics.
- **No OCSF class for `device_events` / `client_events`.** Mist's `CLIENT_*` and
  device event taxonomy is large and mostly operational rather than security
  telemetry; mapping it wholesale to 4001 Network Activity or 3002 would invent
  semantics Mist does not provide. These stay as enriched JSON.
- **No lookups.** Mist event `type` codes are not expanded to descriptions. Mist
  publishes them at `/api/v1/const/device_events` and
  `/api/v1/const/client_events`; a future version could ship them as CSV lookups.

---

## 8. Development

```
default/                     the pack — this is what ships
  jobs.yml                   8 REST Collectors
  breakers.yml               Juniper Mist API Ruleset
  vars.yml                   6 Variables
  outputs.yml                empty on purpose (uses Worker Group destinations)
  samples.yml                generated index of data/samples/
  pack.yml                   pack identity
  pipelines/
    route.yml                Route table
    cribl_mist_parse/        pre-processing
    cribl_mist_events/       JSON output
    cribl_mist_ocsf/         OCSF 1.4 output
data/samples/                generated sample events
tools/
  build-samples.py           regenerate samples from the Mist OpenAPI spec
  render-conf.py             regenerate conf/ paste-ready JSON
  validate-schema.py         validate against a real Cribl install's JSON Schemas
  simulate.js                offline dry run + assertions
  build-pack.sh              produce the .crbl
conf/                        GITIGNORED — generated paste-ready JSON
```

### Regenerating everything

```bash
curl -sSL -o /tmp/mist.openapi.json \
  https://raw.githubusercontent.com/Mist-Automation-Programmability/mist_openapi/main/mist.openapi.json

python3 tools/build-samples.py /tmp/mist.openapi.json   # data/samples + samples.yml
python3 tools/render-conf.py                            # conf/
python3 tools/validate-schema.py                        # vs Cribl's own JSON Schemas
node    tools/simulate.js                               # dry run, exits non-zero on failure
node    tools/simulate.js ocsf
bash    tools/build-pack.sh                             # cribl-mist-rest-io_<version>.crbl
```

`tools/build-pack.sh` uses **GNU tar (`gtar`)**. macOS/BSD `tar` writes archives
Cribl refuses to install, so the script fails fast if `gtar` is missing
(`brew install gnu-tar`).

### Validating against Cribl's own schemas

On install, Cribl validates the pack with AJV and surfaces only the failing
keyword — e.g. `failed to install: should be equal to one of the allowed values`
— with no field path, which is unusable for debugging. Every Cribl install ships
the same schemas on disk:

```
$CRIBL_HOME/default/cribl/collectors/<type>/conf.schema.json
$CRIBL_HOME/default/cribl/functions/<id>/conf.schema.json
```

`tools/validate-schema.py` validates the pack against those and prints the exact
JSON pointer. It runs as part of `build-pack.sh` and skips itself (exit 0) when
there is no local install:

```bash
python3 -m pip install --user jsonschema
CRIBL_HOME=/opt/cribl python3 tools/validate-schema.py
```

It covers collector confs and Function confs — that is where the enums are. The
schemas for `pack.yml`, `vars.yml`, `breakers.yml` and the job wrapper
(`schedule`/`input`) live inside Cribl's bundled JS, not on disk, so those are
still validated only by an actual install.

### Bump checklist

`version` must match in `package.json` and `default/pack.yml`. Cribl decides
whether to upgrade an installed pack from that field.

---

## 9. Changelog

### 1.0.0

- Initial release. Eight REST Collectors covering audit logs, alarms, device
  events, client events, NAC/Access Assurance events, device inventory, device
  stats and site rogue detections.
- `Juniper Mist API Ruleset` event breaker handling the `results` search
  envelope, bare-array responses and `{"detail": ...}` API error bodies.
- `cribl_mist_parse` / `cribl_mist_events` / `cribl_mist_ocsf` pipelines with
  OCSF 1.4 mappings to 6003 API Activity, 2004 Detection Finding and 3002
  Authentication.
- Six Pack Variables covering regional clouds, org id, page size, lookback,
  output format and target index.
- `tools/` — sample generation from the Mist OpenAPI spec, `conf/` renderer,
  schema validator against a local Cribl install, offline simulator with OCSF
  assertions, and the `.crbl` build script.

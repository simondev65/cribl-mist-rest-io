# Juniper Mist REST Collector IO

A Cribl Stream Pack that collects Juniper Mist Cloud telemetry over the Mist REST
API, normalizes it, and optionally reshapes it for Splunk or into OCSF 1.4.

Eight scheduled REST Collectors, a Mist-aware Event Breaker ruleset, three
Pipelines and a Route table. Everything is parameterised through Pack Variables,
so the only per-org setup is an API token, an org id and your regional cloud
hostname.

---

## Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [What's included](#whats-included)
5. [Output formats](#output-formats)
6. [Field reference](#field-reference)
7. [OCSF 1.4 coverage](#ocsf-14-coverage)
8. [API request budget](#api-request-budget)
9. [Limitations](#limitations)
10. [Support](#support)

---

## Requirements

| Requirement | Detail |
|---|---|
| Cribl Stream | 4.17.0 or later |
| Mist API token | Org-scoped read token, stored as a Cribl **text secret** named `mist_api_token` in the Worker Group that runs the pack |
| Mist org id | Your Organization UUID — visible in the Mist portal URL under *Organization → Settings*, or via `GET /api/v1/self` |
| Mist cloud hostname | e.g. `api.mist.com`, `api.eu.mist.com` — see [Regional clouds](#regional-clouds) |
| Network | Outbound HTTPS/443 from the Worker Group to your Mist cloud |

The pack issues `GET` requests only.

---

## Installation

### 1. Install the pack

Download the latest `.crbl` from
[Releases](https://github.com/simondev65/cribl-mist-rest-io/releases), then
*Processing → Packs → Add Pack → Import from File*.

Keep the `cribl-mist-rest-io_<version>.crbl` filename. Cribl derives the pack id
from it, so renaming the file renames the pack.

### 2. Create the `mist_api_token` secret

The token is **not** a Pack Variable. It lives as a Cribl text secret in the Worker
Group that runs the pack, and the collectors read it at request time with
`C.Secret`. Secrets are group-scoped, so this is a per-group step.

| Deployment | Path |
|---|---|
| Distributed / Cribl Cloud | *Worker Group → `<your group>` → Group Settings → Security → Secrets → Add Secret* |
| Single instance | *Settings → Security → Secrets → Add Secret* |

| Field | Value |
|---|---|
| Secret type | **Text** |
| Secret name | `mist_api_token` — exact match required |
| Value | the Mist API token, pasted raw: no `Token ` prefix, no quotes, no trailing whitespace |

Create the token in the Mist portal under *My Account → API Tokens → Create Token*
with read access to the org you want to collect.

A missing or misnamed secret makes the `Authorization` header render as the literal
string `Token undefined`, which Mist answers with `401`. The pack surfaces that as a
`mist:api_error` event rather than as an empty result. Rotating the token is a
one-field edit on the secret — no collector change, no reinstall.

### 3. Set the required Variables

*Pack Settings → Variables*: set `mist_api_host` and `mist_org_id`. Without them,
every collector URL resolves to `https://undefined/api/v1/orgs/undefined/...`.

### 4. Commit and Deploy the Worker Group

Do this **before** running any collector. The collectors live inside the pack, and a
pack-scoped collector only runs on a Worker that has the pack loaded. Saving the
pack on the Leader is not enough: the collect task fails with `Contextualized
InputMgr not found for pack: <pack_id>` until the pack has been deployed.

### 5. Preview one collector

Open `in_mist_org_audit_logs` → **Run → Preview**. That fires a single request and
shows the broken-out events without writing anything downstream. Confirm you see
one event per audit entry, not one event per HTTP response.

### 6. Enable the collectors you want

All eight ship with `schedule.enabled: false`. Enable only the datasets you need —
each one spends part of the same hourly API budget.

### 7. Route the output

The pack's Routes use `output: __group`, so events return to the Worker Group's own
Routes and Destinations. Filter at group level on
`__packsource == 'cribl-mist-rest-io.mist-api'`, on `logtype`, or on `sourcetype`.

---

## Configuration

*Pack Settings → Variables*.

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `mist_api_host` | `api.mist.com` | **yes** | Your regional Mist cloud hostname, no scheme, no trailing slash |
| `mist_org_id` | `YOUR_MIST_ORG_ID` | **yes** | Mist Organization UUID |
| `mist_page_limit` | `1000` | no | Records per request on the six `/search`-style collectors. Mist caps most endpoints at 1000; lower it if you see `400`s or timeouts |
| `mist_lookback_seconds` | `1200` | no | Collection window for `in_mist_site_rogue_events`, which has no state tracking. Keep it larger than that collector's cron interval |
| `mist_output_format` | `passthru` | no | `passthru`, `splunk` or `ocsf`. See [Output formats](#output-formats) — setting this alone is not sufficient |
| `mist_splunk_index` | `juniper_mist` | no | Value written to `index` on the Splunk output path. Set to `''` to leave `index` unset |

`mist_page_limit` does **not** apply to `in_mist_org_audit_logs` or
`in_mist_org_device_stats`. Cribl's Page/Size pagination fields are static numbers
rather than expressions, so those two carry the record limit in *Pagination →
Record limit* on the collector itself.

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

## What's included

### Collectors

| Collector | Endpoint | Pagination | State tracking | Cron |
|---|---|---|---|---|
| `in_mist_org_audit_logs` | `/orgs/<org>/logs` | Page/Size | yes | `*/5 * * * *` |
| `in_mist_org_alarms` | `/orgs/<org>/alarms/search` | Response body | yes | `*/5 * * * *` |
| `in_mist_org_device_events` | `/orgs/<org>/devices/events/search` | Response body | yes | `*/5 * * * *` |
| `in_mist_org_client_events` | `/orgs/<org>/clients/events/search` | Response body | yes | `*/5 * * * *` |
| `in_mist_org_nac_client_events` | `/orgs/<org>/nac_clients/events/search` | Response body | yes | `*/5 * * * *` |
| `in_mist_org_device_inventory` | `/orgs/<org>/devices/search` | Response body | no | `17 * * * *` |
| `in_mist_org_device_stats` | `/orgs/<org>/stats/devices` | Page/Size | no | `*/15 * * * *` |
| `in_mist_site_rogue_events` | `/sites/<site>/rogues/events/search` | Response body | no | `*/15 * * * *` |

Org-level endpoints are used wherever Mist provides them, since they already cover
every site in one request. Rogue AP detections have no org-level equivalent, so
`in_mist_site_rogue_events` uses HTTP Discovery against `/orgs/<org>/sites` and
issues one collect task per site. `in_mist_org_device_inventory` uses Item List
discovery because the endpoint's `type` parameter has no `all` value — it collects
`ap`, `switch` and `gateway` separately.

State tracking on the event streams records the latest `_time` seen and starts the
next run from there, so a missed or skipped run backfills instead of leaving a hole.
The two snapshot collectors (inventory, stats) have it off by design — every run is
a full snapshot.

### Event Breaker

One ruleset, `Juniper Mist API Ruleset`, three rules in order:

| Rule | Condition | Behaviour |
|---|---|---|
| Mist API Error | body starts with `{"detail":` | whole payload as one event, tagged `mist_api_error: true` |
| Mist Search Envelope | body contains `"results"` | one event per record, `_time` from `timestamp` |
| Mist Bare Array | fallback | one event per array element, `_time` from `last_seen` |

API errors stay visible: a `401` from a rotated token or a `429` from
over-collection becomes a searchable `mist:api_error` event rather than silently
looking like "no data".

### Pipelines

| Pipeline | Role |
|---|---|
| `cribl_mist_parse` | Pre-processing on every collector, and the only one always in play. Sets `_time`, `sourcetype`, `logtype`, `vendor`/`product`, `host` and the org/site ids. |
| `cribl_mist_to_splunk` | Optional Splunk shape. Serializes the record to JSON in `_raw`, sets a flat `sourcetype = juniper_mist`, applies `index`. |
| `cribl_mist_ocsf` | Optional OCSF 1.4 output. |

---

## Output formats

Out of the box both shaping Routes are **disabled**, so Mist events leave the pack
exactly as `cribl_mist_parse` produced them: enriched fields on the event, original
`_raw`, no serialization, no destination-specific opinion. The pack normalizes; you
choose the shape.

| # | Route | Enabled | Pipeline |
|---|---|---|---|
| 1 | `mist_ocsf` | no | `cribl_mist_ocsf` |
| 2 | `mist_to_splunk` | no | `cribl_mist_to_splunk` |
| 3 | `mist_passthru` | **yes** | `passthru` (built-in) |
| 4 | `default` | yes | `passthru` (built-in) |

Selecting a shape takes two changes, not one:

| Want | Enable Route | Set `mist_output_format` |
|---|---|---|
| enriched pass-through | *(nothing — this is the default)* | `passthru` |
| Splunk-shaped JSON | `mist_to_splunk` | `splunk` |
| OCSF 1.4 | `mist_ocsf` | `ocsf` |

Either half on its own is a no-op: the Routes filter on the Variable, and the
Variable has no effect while the Route is disabled. Route 3 is deliberately not
gated on the Variable, so it catches any Mist event the shaping Routes did not —
including a half-finished switch. Nothing is ever left unrouted.

All Mist Routes are `final`, so exactly one fires per event, and Route 1 precedes
Route 2: with both enabled and `mist_output_format = 'ocsf'`, the four OCSF-capable
datasets become OCSF and the rest pass through. One Variable means one shape —
there is no single setting for "OCSF for security datasets, Splunk JSON for the
rest".

---

## Field reference

Every event leaves `cribl_mist_parse` with these fields, on all three output paths:

| Field | Value |
|---|---|
| `_time` | from `timestamp`, `last_seen`, `modified_time` or `created_time`, whichever the endpoint provides |
| `sourcetype` | `mist:<dataset>`, or `juniper_mist` on the Splunk path, or `ocsf:<class_name>` on the OCSF path |
| `logtype` | short dataset label — see the table below |
| `vendor` / `product` | `Juniper` / `Mist` |
| `host` | best-effort: hostname, MAC, reporting AP, or the admin who generated an audit entry |
| `mist_org_id` | org UUID |
| `mist_site_id` / `mist_site_name` | present on site-scoped datasets |
| `mist_dataset` | which collector produced the event |
| `__packsource` | `cribl-mist-rest-io.mist-api` on every event |

### `logtype`

| `mist_dataset` | `logtype` |
|---|---|
| `audit_logs` | `mist_audit` |
| `alarms` | `mist_alarms` |
| `device_events` | `mist_device_events` |
| `client_events` | `mist_client_events` |
| `nac_client_events` | `mist_nac_client_events` |
| `device_inventory` | `mist_inventory` |
| `device_stats` | `mist_device_stats` |
| `rogue_events` | `mist_rogue_events` |
| `api_error` | `mist_api_error` |

A collector you add yourself with an unmapped `mist_dataset` falls back to
`mist_<dataset>`, so the field is never empty.

---

## OCSF 1.4 coverage

| Mist dataset | OCSF class | Notes |
|---|---|---|
| `audit_logs` | **6003** API Activity | `activity_id` inferred from the message text (create/read/update/delete), defaulting to `99` Other rather than guessing |
| `alarms` | **2004** Detection Finding | `severity_id` from Mist `severity`; `status_id` from `status`/`acked`; `finding_info.src_url` deep-links to the Mist portal |
| `rogue_events` | **2004** Detection Finding | fixed `severity_id: 3` — see [Limitations](#limitations) |
| `nac_client_events` | **3002** Authentication | `status_id` and `severity_id` from the `NAC_CLIENT_*` event type; `auth_protocol` from `auth_type` |
| `device_events`, `client_events`, `device_inventory`, `device_stats`, `api_error` | *(none)* | never routed to OCSF; they keep the enriched form or take the Splunk shape |

Anything Mist sends that has no OCSF home lands under `unmapped`, so nothing is lost
in translation.

---

## API request budget

A Mist API token is limited to **5000 calls per hour**, and that budget is shared by
everything using the token. Rough cost per run:

| Collector group | Cost per run |
|---|---|
| Org-level event collectors | 1 call + 1 per extra page |
| Device inventory | 3 calls (one per device type) + pages |
| Device stats | 1 call + 1 per 100 devices |
| Site rogue events | 1 discovery call + 1 call per site + pages |

Five org-level collectors on `*/5` is 12 runs/hour each — about 60 calls/hour before
pagination. The site fan-out is the one to watch: 200 sites on `*/15` is
4 × 201 ≈ 800 calls/hour. Count your sites before lowering its cron interval.

Every collector retries `429` with exponential backoff honouring `retry-after`, but
staying inside the budget beats retrying.

---

## Limitations

- **Rogue events carry no rogue class.** A Mist rogue event is only
  `{ap, bssid, ssid, channel, rssi, timestamp}`. The class
  (`honeypot | lan | others | spoof`) is a *request* filter that Mist does not echo
  back, so severity cannot be derived from the record and defaults to `3` (Medium).
  For per-class severity, clone `in_mist_site_rogue_events`, add `type=<class>` to
  its request parameters, and add a matching `mist_rogue_type` metadata field — the
  OCSF pipeline already reads it when present.
- **`in_mist_site_rogue_events` has no state tracking**, because its per-site
  fan-out would require state keyed per site. It uses a fixed
  `mist_lookback_seconds` window, deliberately wider than its cron interval. Expect
  a small number of duplicate events at the window boundary; dedupe on `bssid` +
  `timestamp` downstream if that matters.
- **NAC events may paginate only one page deep.** Mist does not declare a `next`
  attribute on `/orgs/<org>/nac_clients/events/search`, unlike every other `/search`
  endpoint, so a run can cap at `mist_page_limit` records. On a `*/5` schedule with
  `limit=1000` that is 12000 events/hour of headroom — raise the frequency or narrow
  the collection with a `type` filter if your org exceeds it.
- **`device_stats` records are large** (10 KB+ each, with nested `ble_stat`,
  `lldp_stats`, `if_stat`). At org scale this is the most expensive dataset by
  volume. Use the endpoint's `fields` parameter to trim it if you only need a few
  metrics.
- **No OCSF class for `device_events` / `client_events`.** Mist's `CLIENT_*` and
  device event taxonomy is large and mostly operational rather than security
  telemetry; mapping it wholesale would invent semantics Mist does not provide.
- **No lookups.** Mist event `type` codes are not expanded to descriptions. Mist
  publishes them at `/api/v1/const/device_events` and `/api/v1/const/client_events`.

---

## Support

This pack is community-supported and not covered by Cribl Support.

| | |
|---|---|
| Author | Simon Duchene — sduchene@cribl.io |
| Issues | [github.com/simondev65/cribl-mist-rest-io/issues](https://github.com/simondev65/cribl-mist-rest-io/issues) |
| License | Apache-2.0 |

Verified on Cribl Stream 4.19.2: all eight collectors, three pipelines, the Event
Breaker ruleset, six Variables and four Routes load with no warnings.

#!/usr/bin/env python3
"""Generate data/samples/*.json and default/samples.yml for cribl-mist-rest-io.

Samples are built from the community Juniper Mist OpenAPI spec so the field
names in the pack's pipelines are never guessed:

    curl -sSL -o /tmp/mist.openapi.json \
      https://raw.githubusercontent.com/Mist-Automation-Programmability/mist_openapi/main/mist.openapi.json
    python3 tools/build-samples.py [/tmp/mist.openapi.json]

Each sample is written POST-BREAKER, i.e. one Cribl event per Mist record, with
the record's fields flattened at the top level (the "Juniper Mist API Ruleset"
breaker sets jsonExtractAll: true) plus _raw and the Collector metadata that
default/pipelines/route.yml filters on. That makes Data Preview behave exactly
like a live collection run.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLE_DIR = os.path.join(ROOT, "data", "samples")
SAMPLES_YML = os.path.join(ROOT, "default", "samples.yml")

PACK_ID = "cribl-mist-rest-io"
PACKSOURCE = "cribl-mist-rest-io.mist-api"

# sample id -> (OpenAPI example name, mist_dataset, extra metadata, description)
# A None example name means the sample is hand-written below in HANDWRITTEN.
SAMPLES = [
    ("mist_audit_logs", "LogsSearchExample", "audit_logs", {},
     "Org admin audit log entries from GET /orgs/<org>/logs"),
    ("mist_alarms", None, "alarms", {},
     "Org alarms from GET /orgs/<org>/alarms/search"),
    ("mist_device_events", "DeviceEventsSearchExample", "device_events", {},
     "AP/switch/gateway events from GET /orgs/<org>/devices/events/search"),
    ("mist_client_events", "ClientEventsSearchExample", "client_events", {},
     "Wireless client events from GET /orgs/<org>/clients/events/search"),
    ("mist_nac_client_events", "EventsNacClientSearchNACclientevents",
     "nac_client_events", {},
     "Access Assurance (NAC) events from GET /orgs/<org>/nac_clients/events/search"),
    ("mist_device_inventory", "DevicesSearchAp", "device_inventory",
     {"mist_device_type": "ap"},
     "Managed AP inventory from GET /orgs/<org>/devices/search?type=ap"),
    ("mist_device_stats", "DevicesArrayStatsOrgAccessPointStats", "device_stats", {},
     "AP health/radio statistics from GET /orgs/<org>/stats/devices"),
    ("mist_site_rogue_events", "RogueEventsSearchExample", "rogue_events",
     {"mist_site_id": "441a1214-6928-442a-8e92-e1d34b8ec6a6",
      "mist_site_name": "Mist Office"},
     "Rogue AP detections from GET /sites/<site>/rogues/events/search"),
    ("mist_api_error", None, "api_error", {"mist_api_error": True},
     "Mist API error bodies (401 bad token, 429 throttled) as tagged by the breaker"),
]

# Synthesised from components/schemas/alarm and components/examples in the spec.
# The spec ships no alarms/search example, so these two records use only fields
# the alarm schema actually declares.
HANDWRITTEN = {
    "mist_alarms": [
        {
            "id": "56bfa7af-b2db-43ee-a4c8-9b820bbba0e1",
            "type": "rogue_ap",
            "group": "security",
            "component": "AP",
            "severity": "critical",
            "status": "open",
            "acked": False,
            "count": 2,
            "timestamp": 1711031354,
            "last_seen": 1711031774,
            "org_id": "b3b9f5e6-67b1-4112-9b4c-6824c565eaeb",
            "site_id": "441a1214-6928-442a-8e92-e1d34b8ec6a6",
            "aps": ["5c5b350e10030"],
            "bssids": ["38ff363c8c4c"],
            "ssids": ["MyHomeNetwork"],
        },
        {
            "id": "9d1c1f70-2ab1-4a63-8a0f-0f6b31b6f4aa",
            "type": "device_down",
            "group": "infrastructure",
            "component": "SW",
            "severity": "warn",
            "status": "resolved",
            "acked": True,
            "acked_time": 1711031352,
            "ack_admin_id": "456b7016-a916-a4b1-78dd-72b947c152b7",
            "ack_admin_name": "Joe",
            "resolved_time": 1711032900,
            "note": "Scheduled maintenance window",
            "count": 1,
            "timestamp": 1711030100,
            "last_seen": 1711030100,
            "org_id": "b3b9f5e6-67b1-4112-9b4c-6824c565eaeb",
            "site_id": "441a1214-6928-442a-8e92-e1d34b8ec6a6",
            "switches": ["ec3eb3000001"],
            "hostnames": ["ME-DC-1-ACC-SW"],
        },
    ],
    "mist_api_error": [
        {"detail": "Authentication credentials were not provided."},
        {"detail": "Request was throttled. Expected available in 3600 seconds."},
    ],
}


def records_from_example(value):
    """A Mist response is either a {..., results: [...]} envelope or a bare array."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("results"), list):
        return value["results"]
    return [value]


def build_events(records, dataset, extra):
    events = []
    for rec in records:
        # Flattened record fields (jsonExtractAll: true) + _raw + metadata.
        ev = dict(rec)
        ev["_raw"] = json.dumps(rec, separators=(",", ":"), sort_keys=True)
        ev["__packsource"] = PACKSOURCE
        ev["mist_dataset"] = dataset
        ev.update(extra)
        events.append(ev)
    return events


def main():
    spec_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mist.openapi.json"
    with open(spec_path) as fh:
        spec = json.load(fh)
    examples = spec["components"]["examples"]

    os.makedirs(SAMPLE_DIR, exist_ok=True)
    index = {}

    for sid, ex_name, dataset, extra, desc in SAMPLES:
        if ex_name is None:
            records = HANDWRITTEN[sid]
        else:
            if ex_name not in examples:
                sys.exit(f"example {ex_name!r} not found in {spec_path}")
            records = records_from_example(examples[ex_name]["value"])

        events = build_events(records, dataset, extra)
        blob = json.dumps(events, indent=2, sort_keys=True) + "\n"
        path = os.path.join(SAMPLE_DIR, sid + ".json")
        with open(path, "w") as fh:
            fh.write(blob)

        index[sid] = {
            "sampleName": sid + ".json",
            "description": desc,
            "tags": ["juniper", "mist", dataset],
            "isPackOnly": True,
            "packId": PACK_ID,
            "size": len(blob.encode("utf-8")),
            "numEvents": len(events),
            "created": 0,
        }
        print(f"  {sid + '.json':32} {len(events):3} events  {index[sid]['size']:7} bytes")

    # samples.yml is a MAP keyed by sample id; the id must equal the file
    # basename or Data Preview fails with "Unable to find sample with id=...".
    lines = [
        "# GENERATED by tools/build-samples.py - do not edit by hand.",
        "# Regenerate with: python3 tools/build-samples.py /tmp/mist.openapi.json",
        "#",
        "# Keyed by sample id; each id must match data/samples/<id>.json.",
        "",
    ]
    for sid, meta in index.items():
        lines.append(f"{sid}:")
        lines.append(f"  sampleName: {meta['sampleName']}")
        lines.append(f"  description: {json.dumps(meta['description'])}")
        lines.append("  tags:")
        for t in meta["tags"]:
            lines.append(f"    - {t}")
        lines.append("  isPackOnly: true")
        lines.append(f"  packId: {meta['packId']}")
        lines.append(f"  size: {meta['size']}")
        lines.append(f"  numEvents: {meta['numEvents']}")
        lines.append("  created: 0")
    with open(SAMPLES_YML, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nwrote {SAMPLES_YML} ({len(index)} samples)")


if __name__ == "__main__":
    main()

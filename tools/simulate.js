#!/usr/bin/env node
/*
 * Offline dry-run of the pack's pipelines against data/samples/.
 *
 *   node tools/simulate.js            # run every sample through parse -> route -> output
 *   node tools/simulate.js ocsf       # force mist_output_format = 'ocsf'
 *
 * This is a deliberately small stand-in for Cribl's expression engine: enough to
 * catch broken field names, bad ternaries and malformed OCSF before touching a
 * Worker Group. It is NOT a Cribl emulator - always confirm in Data Preview.
 *
 * Implemented: eval (add/remove/keep), serialize (json), drop, comment.
 * Wildcard lists use Cribl's first-match-wins semantics, so exclusions must be
 * listed before '*'.
 */

'use strict';
const fs = require('fs');
const path = require('path');
const ROOT = path.dirname(__dirname);

// ---------------------------------------------------------------- yaml loader
let yaml;
try {
  yaml = require('yaml');
} catch (e) {
  // No node yaml module here; shell out to python3 + pyyaml instead.
  const { execFileSync } = require('child_process');
  yaml = {
    parse(text) {
      const out = execFileSync('python3', ['-c',
        'import sys,yaml,json;json.dump(yaml.safe_load(sys.stdin.read()),sys.stdout)'],
        { input: text, maxBuffer: 64 * 1024 * 1024 });
      return JSON.parse(out.toString());
    },
  };
}
const loadYaml = (rel) => yaml.parse(fs.readFileSync(path.join(ROOT, rel), 'utf8'));

// ------------------------------------------------------------ C.vars / C.Secret
const varsYml = loadYaml('default/vars.yml');
const vars = {};
for (const [k, v] of Object.entries(varsYml)) {
  // vars.yml values are JS expressions held as strings.
  vars[k] = new Function('return (' + v.value + ');')();
}
const C = {
  vars,
  Secret: () => ({ value: 'REDACTED-TEST-TOKEN' }),
  Encode: { uri: encodeURIComponent },
  Time: { strftime: (t, f) => new Date(t * 1000).toISOString() },
};

// -------------------------------------------------------------- expression eval
const GLOBALS = { C, Math, Number, String, Boolean, Date, Object, Array, JSON, RegExp, isNaN, parseInt, parseFloat, undefined: undefined };
const cache = new Map();
function evaluate(expr, event) {
  let fn = cache.get(expr);
  if (!fn) {
    fn = new Function('__scope', 'with(__scope){ return (' + expr + '); }');
    cache.set(expr, fn);
  }
  // Proxy so unknown identifiers resolve to undefined instead of throwing,
  // which is how Cribl's engine behaves.
  const scope = new Proxy(event, {
    has: () => true,
    get: (t, k) => (k in t ? t[k] : (k in GLOBALS ? GLOBALS[k] : undefined)),
  });
  return fn(scope);
}

// ------------------------------------------------------------- wildcard lists
function globToRe(pat) {
  return new RegExp('^' + pat.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$');
}
// First match wins: '!foo' excludes, 'foo' includes, no match -> excluded.
function selectFields(keys, patterns) {
  const rules = patterns.map((p) => (p.startsWith('!')
    ? { re: globToRe(p.slice(1)), keep: false }
    : { re: globToRe(p), keep: true }));
  return keys.filter((k) => {
    for (const r of rules) if (r.re.test(k)) return r.keep;
    return false;
  });
}

// ------------------------------------------------------------------- functions
function applyFunction(fn, event) {
  if (fn.disabled) return event;
  if (fn.id === 'comment') return event;
  if (fn.filter !== undefined && !evaluate(fn.filter, event)) return event;
  const conf = fn.conf || {};

  if (fn.id === 'drop') return null;

  if (fn.id === 'eval') {
    for (const a of conf.add || []) {
      const v = evaluate(a.value, event);
      if (v === undefined) delete event[a.name];
      else event[a.name] = v;
    }
    const removes = conf.remove || [];
    const keeps = conf.keep || [];
    if (removes.length) {
      const doomed = selectFields(Object.keys(event), removes);
      const spared = new Set(selectFields(Object.keys(event), keeps));
      for (const k of doomed) if (!spared.has(k)) delete event[k];
    }
    return event;
  }

  if (fn.id === 'serialize') {
    if (conf.type !== 'json') throw new Error('simulate.js only handles serialize type=json');
    const chosen = selectFields(Object.keys(event), conf.fields || ['*']);
    const doc = {};
    for (const k of chosen) doc[k] = event[k];
    event[conf.dstField || '_raw'] = JSON.stringify(doc);
    return event;
  }

  throw new Error('simulate.js does not implement function id=' + fn.id);
}

function runPipeline(id, event) {
  const conf = loadYaml(`default/pipelines/${id}/conf.yml`);
  let e = event;
  for (const fn of conf.functions) {
    e = applyFunction(fn, e);
    if (e === null) return null;
  }
  return e;
}

// ----------------------------------------------------------------------- main
const forceFormat = process.argv[2];
if (forceFormat) vars.mist_output_format = forceFormat;

const routes = loadYaml('default/pipelines/route.yml').routes;
const sampleDir = path.join(ROOT, 'data', 'samples');
const files = fs.readdirSync(sampleDir).filter((f) => f.endsWith('.json')).sort();

let total = 0, dropped = 0, problems = 0;
console.log(`mist_output_format = ${JSON.stringify(vars.mist_output_format)}\n`);

for (const file of files) {
  const events = JSON.parse(fs.readFileSync(path.join(sampleDir, file), 'utf8'));
  const seen = {};
  for (const raw of events) {
    total++;
    let e;
    try {
      e = runPipeline('cribl_mist_parse', Object.assign({}, raw));
    } catch (err) {
      problems++; console.log(`  !! ${file}: parse threw: ${err.message}`); continue;
    }
    if (!e) { dropped++; continue; }

    const route = routes.find((r) => !r.disabled && evaluate(r.filter, e));
    if (!route) { problems++; console.log(`  !! ${file}: no route matched`); continue; }
    if (!route.pipeline) { seen['(pass-through)'] = (seen['(pass-through)'] || 0) + 1; continue; }

    try {
      e = runPipeline(route.pipeline, e);
    } catch (err) {
      problems++; console.log(`  !! ${file}: ${route.pipeline} threw: ${err.message}`); continue;
    }
    if (!e) { dropped++; continue; }

    const key = route.pipeline;
    seen[key] = (seen[key] || 0) + 1;

    // Validate the emitted payload.
    let doc;
    try { doc = JSON.parse(e._raw); }
    catch (err) { problems++; console.log(`  !! ${file}: _raw is not valid JSON`); continue; }

    if (!(e._time > 1000000000 && e._time < 4000000000)) {
      problems++; console.log(`  !! ${file}: implausible _time=${e._time}`);
    }
    if (key === 'cribl_mist_ocsf') {
      for (const req of ['class_uid', 'category_uid', 'activity_id', 'type_uid', 'severity_id', 'time', 'metadata']) {
        if (doc[req] === undefined || doc[req] === null) {
          problems++; console.log(`  !! ${file}: OCSF missing ${req}`);
        }
      }
      if (doc.type_uid !== doc.class_uid * 100 + doc.activity_id) {
        problems++; console.log(`  !! ${file}: type_uid ${doc.type_uid} != class_uid*100+activity_id`);
      }
      if (!(doc.time > 1e12 && doc.time < 4e12)) {
        problems++; console.log(`  !! ${file}: OCSF time not ms epoch: ${doc.time}`);
      }
    }
    // Nothing internal should survive the whitelist.
    for (const k of Object.keys(e)) {
      if (k !== '_raw' && k !== '_time' && k.startsWith('_')) {
        problems++; console.log(`  !! ${file}: internal field leaked: ${k}`);
      }
    }
    if (!seen.__firstDoc) {
      seen.__firstDoc = true;
      const preview = JSON.stringify(doc);
      console.log(`  ${file}`);
      console.log(`     -> ${key} | sourcetype=${e.sourcetype} host=${e.host} index=${e.index}`);
      console.log(`     -> ${preview.length > 240 ? preview.slice(0, 240) + ' ...' : preview}`);
    }
  }
  delete seen.__firstDoc;
  const summary = Object.entries(seen).map(([k, v]) => `${k}=${v}`).join(' ');
  if (summary) console.log(`     routed: ${summary}\n`);
}

console.log(`${total} events, ${dropped} dropped, ${problems} problem(s)`);
process.exit(problems ? 1 : 0);

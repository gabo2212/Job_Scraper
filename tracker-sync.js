/*
 * tracker-sync.js — pure logic for the dashboard's job-tracking state and its
 * opt-in sync to a secret GitHub Gist. No DOM, no globals: loads in the browser
 * (window.TrackerSync) and in Node (module.exports) so it can be unit-tested.
 *
 * Tracker model (what gets synced; also stored per-browser in localStorage):
 *   { v: 1,
 *     jobs:    { [urlHash]: { url, status, statusAt, stars, starsAt, note, noteAt,
 *                             events: { [id]: { ts, text, deletedAt? } },
 *                             updatedAt } },
 *     blocked: { [companyLower]: { on, at } } }
 *
 * Merge = per-field last-writer-wins by *At timestamps (so a note edited on the
 * phone and a status set on the desktop both survive), events are a union by id
 * with tombstones, blocked companies are LWW. Clearing a value is an ordinary
 * write (value null/0/'' with a fresh At), i.e. the tombstone for that field.
 * merge(a, b) is commutative, associative and idempotent, so devices converge
 * no matter the order or repetition of syncs.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.TrackerSync = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var LEGACY_AT = 1;              // legacy (pre-sync) data is older than any real edit
  var FILE_NAME = 'job-tracker-state.json';
  var GIST_DESC = 'job-tracker-sync';
  var API_BASE = 'https://api.github.com';

  // ---------- small utils ----------
  function hash53(str, seed) {
    var h1 = 0xdeadbeef ^ (seed || 0), h2 = 0x41c6ce57 ^ (seed || 0);
    for (var i = 0; i < str.length; i++) {
      var ch = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return 4294967296 * (2097151 & h2) + (h1 >>> 0);
  }
  function hashUrl(url) { return hash53(String(url || '')).toString(16).padStart(14, '0'); }

  function stableStringify(v) {
    if (v === null || typeof v !== 'object') return JSON.stringify(v === undefined ? null : v);
    if (Array.isArray(v)) return '[' + v.map(stableStringify).join(',') + ']';
    return '{' + Object.keys(v).sort().map(function (k) {
      return JSON.stringify(k) + ':' + stableStringify(v[k]);
    }).join(',') + '}';
  }

  function num(n) { return typeof n === 'number' && isFinite(n) ? n : 0; }
  function isObj(o) { return o && typeof o === 'object' && !Array.isArray(o); }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  // ---------- model ----------
  function emptyTracker() { return { v: 1, jobs: {}, blocked: {} }; }

  function eventId(url, ev) {
    return 'l' + hash53(String(url) + '|' + num(ev && ev.ts) + '|' + String((ev && ev.text) || '')).toString(16);
  }

  function normalizeEntry(e) {
    var out = { url: typeof e.url === 'string' ? e.url : '', events: {} };
    ['status', 'stars', 'note'].forEach(function (f) {
      if (e[f + 'At'] !== undefined || e[f] !== undefined) {
        out[f] = e[f] === undefined ? null : e[f];
        out[f + 'At'] = num(e[f + 'At']);
      }
    });
    if (out.status !== undefined && out.status !== null && typeof out.status !== 'string') out.status = null;
    if (out.stars !== undefined && out.stars !== null) out.stars = Math.max(0, Math.min(5, Math.round(num(out.stars))));
    if (out.note !== undefined && out.note !== null && typeof out.note !== 'string') out.note = String(out.note);
    if (isObj(e.events)) {
      Object.keys(e.events).forEach(function (id) {
        var ev = e.events[id];
        if (!isObj(ev)) return;
        var o = { ts: num(ev.ts), text: String(ev.text == null ? '' : ev.text) };
        if (num(ev.deletedAt) > 0) o.deletedAt = num(ev.deletedAt);
        out.events[id] = o;
      });
    }
    out.updatedAt = num(e.updatedAt);
    return out;
  }

  // Defensive copy: anything malformed is dropped rather than trusted.
  function normalizeTracker(t) {
    var out = emptyTracker();
    if (!isObj(t)) return out;
    if (isObj(t.jobs)) {
      Object.keys(t.jobs).forEach(function (k) {
        if (isObj(t.jobs[k])) out.jobs[k] = normalizeEntry(t.jobs[k]);
      });
    }
    if (isObj(t.blocked)) {
      Object.keys(t.blocked).forEach(function (name) {
        var b = t.blocked[name];
        if (isObj(b)) out.blocked[name] = { on: !!b.on, at: num(b.at) };
      });
    }
    return out;
  }

  function entryUpdatedAt(e) {
    var m = num(e.updatedAt);
    ['statusAt', 'starsAt', 'noteAt'].forEach(function (f) { m = Math.max(m, num(e[f])); });
    Object.keys(e.events).forEach(function (id) {
      m = Math.max(m, num(e.events[id].deletedAt), num(e.events[id].ts));
    });
    return m;
  }

  // Pick the newer {value, at}; on a tie the larger serialized value wins so
  // the result never depends on argument order (commutative).
  function pickField(a, b, f) {
    var aHas = a[f + 'At'] !== undefined, bHas = b[f + 'At'] !== undefined;
    if (!aHas && !bHas) return null;
    if (aHas && !bHas) return { v: a[f], at: a[f + 'At'] };
    if (bHas && !aHas) return { v: b[f], at: b[f + 'At'] };
    if (a[f + 'At'] !== b[f + 'At']) return a[f + 'At'] > b[f + 'At'] ? { v: a[f], at: a[f + 'At'] } : { v: b[f], at: b[f + 'At'] };
    return JSON.stringify(a[f]) >= JSON.stringify(b[f]) ? { v: a[f], at: a[f + 'At'] } : { v: b[f], at: b[f + 'At'] };
  }

  function mergeEntry(a, b) {
    var out = { url: a.url || b.url || '', events: {} };
    ['status', 'stars', 'note'].forEach(function (f) {
      var p = pickField(a, b, f);
      if (p) { out[f] = p.v; out[f + 'At'] = p.at; }
    });
    var ids = {};
    Object.keys(a.events).forEach(function (i) { ids[i] = 1; });
    Object.keys(b.events).forEach(function (i) { ids[i] = 1; });
    Object.keys(ids).forEach(function (id) {
      var x = a.events[id], y = b.events[id];
      var base = x || y;
      var ev = { ts: base.ts, text: base.text };
      var del = Math.max(num(x && x.deletedAt), num(y && y.deletedAt));
      if (del > 0) ev.deletedAt = del;
      out.events[id] = ev;
    });
    out.updatedAt = Math.max(num(a.updatedAt), num(b.updatedAt));
    out.updatedAt = Math.max(out.updatedAt, entryUpdatedAt(out));
    return out;
  }

  function mergeTrackerState(local, remote) {
    var a = normalizeTracker(local), b = normalizeTracker(remote);
    var out = emptyTracker();
    var keys = {};
    Object.keys(a.jobs).forEach(function (k) { keys[k] = 1; });
    Object.keys(b.jobs).forEach(function (k) { keys[k] = 1; });
    Object.keys(keys).forEach(function (k) {
      out.jobs[k] = a.jobs[k] && b.jobs[k] ? mergeEntry(a.jobs[k], b.jobs[k])
        : clone(a.jobs[k] || b.jobs[k]);
    });
    var names = {};
    Object.keys(a.blocked).forEach(function (k) { names[k] = 1; });
    Object.keys(b.blocked).forEach(function (k) { names[k] = 1; });
    Object.keys(names).forEach(function (n) {
      var x = a.blocked[n], y = b.blocked[n];
      if (x && y) {
        out.blocked[n] = x.at !== y.at ? (x.at > y.at ? { on: x.on, at: x.at } : { on: y.on, at: y.at })
          : { on: x.on || y.on, at: x.at };
      } else {
        out.blocked[n] = clone(x || y);
      }
    });
    return out;
  }

  // ---------- view <-> tracker ----------
  // The dashboard keeps reading the legacy shapes (state.triage / notes / stars /
  // timeline / blockedCompanies); these convert to and from the tracker.
  function emptyView() { return { triage: {}, notes: {}, stars: {}, timeline: {}, blockedCompanies: [] }; }

  function materialize(tracker) {
    var t = normalizeTracker(tracker), v = emptyView();
    Object.keys(t.jobs).forEach(function (k) {
      var e = t.jobs[k];
      if (!e.url) return;
      if (e.status) v.triage[e.url] = e.status;
      if (e.stars) v.stars[e.url] = e.stars;
      if (e.note) v.notes[e.url] = e.note;
      var evs = Object.keys(e.events).filter(function (id) { return !e.events[id].deletedAt; })
        .map(function (id) { return { id: id, ts: e.events[id].ts, text: e.events[id].text }; })
        .sort(function (x, y) { return (x.ts - y.ts) || (x.id < y.id ? -1 : x.id > y.id ? 1 : 0); });
      if (evs.length) v.timeline[e.url] = evs;
    });
    v.blockedCompanies = Object.keys(t.blocked).filter(function (n) { return t.blocked[n].on; }).sort();
    return v;
  }

  function withEventIds(url, list) {
    return (list || []).map(function (ev) {
      return { id: ev.id || eventId(url, ev), ts: num(ev.ts), text: String(ev.text == null ? '' : ev.text) };
    });
  }

  function viewFromLegacy(legacy) {
    var l = isObj(legacy) ? legacy : {};
    var v = emptyView();
    if (isObj(l.triage)) Object.keys(l.triage).forEach(function (u) { if (l.triage[u]) v.triage[u] = String(l.triage[u]); });
    if (isObj(l.notes)) Object.keys(l.notes).forEach(function (u) { if (l.notes[u]) v.notes[u] = String(l.notes[u]); });
    if (isObj(l.stars)) Object.keys(l.stars).forEach(function (u) { if (num(l.stars[u]) > 0) v.stars[u] = num(l.stars[u]); });
    if (isObj(l.timeline)) Object.keys(l.timeline).forEach(function (u) {
      if (Array.isArray(l.timeline[u]) && l.timeline[u].length) v.timeline[u] = withEventIds(u, l.timeline[u]);
    });
    if (Array.isArray(l.blockedCompanies)) v.blockedCompanies = l.blockedCompanies.filter(Boolean).map(String);
    return v;
  }

  function touch(tr, url) {
    var k = hashUrl(url);
    if (!tr.jobs[k]) tr.jobs[k] = { url: url, events: {}, updatedAt: 0 };
    if (!tr.jobs[k].url) tr.jobs[k].url = url;
    return tr.jobs[k];
  }

  // Apply the edits that turn `baseline` into `current` onto `tracker` (returns a
  // new tracker). Baseline is what this tab last saw, NOT the latest stored
  // tracker, so a stale tab only ever writes its own changes and can't revert
  // another tab's.
  function applyViewChanges(tracker, baseline, current, now) {
    var tr = normalizeTracker(tracker), b = baseline || emptyView(), c = current || emptyView();
    var urls = {};
    ['triage', 'notes', 'stars', 'timeline'].forEach(function (f) {
      Object.keys(b[f] || {}).forEach(function (u) { urls[u] = 1; });
      Object.keys(c[f] || {}).forEach(function (u) { urls[u] = 1; });
    });
    Object.keys(urls).forEach(function (u) {
      var e = null;
      function ent() { return e || (e = touch(tr, u)); }
      var bs = (b.triage || {})[u] || null, cs = (c.triage || {})[u] || null;
      if (bs !== cs) { ent().status = cs; ent().statusAt = now; }
      var br = num((b.stars || {})[u]), cr = num((c.stars || {})[u]);
      if (br !== cr) { ent().stars = cr || null; ent().starsAt = now; }
      var bn = (b.notes || {})[u] || '', cn = (c.notes || {})[u] || '';
      if (bn !== cn) { ent().note = cn || null; ent().noteAt = now; }
      var bev = {}, cev = {};
      withEventIds(u, (b.timeline || {})[u]).forEach(function (ev) { bev[ev.id] = ev; });
      withEventIds(u, (c.timeline || {})[u]).forEach(function (ev) { cev[ev.id] = ev; });
      Object.keys(cev).forEach(function (id) {
        if (!bev[id]) {
          var ex = ent().events[id];
          // Re-adding an id deleted earlier stays deleted (tombstone wins); ids
          // are unique per event so this only matters for legacy duplicates.
          if (!ex) ent().events[id] = { ts: cev[id].ts, text: cev[id].text };
        }
      });
      Object.keys(bev).forEach(function (id) {
        if (!cev[id]) {
          var ex2 = ent().events[id] || { ts: bev[id].ts, text: bev[id].text };
          ex2.deletedAt = now;
          ent().events[id] = ex2;
        }
      });
      if (e) e.updatedAt = Math.max(num(e.updatedAt), now);
    });
    var bb = {}, cb = {};
    (b.blockedCompanies || []).forEach(function (n) { bb[n] = 1; });
    (c.blockedCompanies || []).forEach(function (n) { cb[n] = 1; });
    Object.keys(cb).forEach(function (n) { if (!bb[n]) tr.blocked[n] = { on: true, at: now }; });
    Object.keys(bb).forEach(function (n) { if (!cb[n]) tr.blocked[n] = { on: false, at: now }; });
    return tr;
  }

  // Legacy (v3 localStorage) → tracker, stamped LEGACY_AT so any real edit,
  // here or on another device, wins over it.
  function migrateLegacy(legacy) {
    return applyViewChanges(emptyTracker(), emptyView(), viewFromLegacy(legacy), LEGACY_AT);
  }

  function viewsEqual(x, y) { return stableStringify(x) === stableStringify(y); }

  // ---------- phone setup link ----------
  function b64encode(str) {
    var s;
    if (typeof Buffer !== 'undefined') s = Buffer.from(str, 'utf8').toString('base64');
    else {
      // NB: don't use escape()/unescape(): the dashboard defines its own global escape().
      var bytes = new TextEncoder().encode(str), bin = '';
      for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      s = btoa(bin);
    }
    return s.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function b64decode(b64) {
    var s = String(b64).replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    if (typeof Buffer !== 'undefined') return Buffer.from(s, 'base64').toString('utf8');
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(out);
  }
  var TOKEN_RE = /^[A-Za-z0-9_\-]{20,255}$/;
  var GIST_ID_RE = /^[A-Fa-f0-9]{8,64}$/;
  function validConfig(cfg) {
    return !!cfg && typeof cfg.token === 'string' && TOKEN_RE.test(cfg.token)
      && typeof cfg.gistId === 'string' && GIST_ID_RE.test(cfg.gistId);
  }
  function makeSetupFragment(cfg) {
    return '#sync=' + b64encode(JSON.stringify({ gistId: cfg.gistId, token: cfg.token }));
  }
  // Returns {gistId, token} or null. Accepts "#sync=..." (or "sync=...").
  function parseSetupFragment(hash) {
    var m = /(?:^#?|[#&])sync=([^&]+)/.exec(String(hash || ''));
    if (!m) return null;
    try {
      var o = JSON.parse(b64decode(m[1]));
      var cfg = { gistId: String(o.gistId || ''), token: String(o.token || '') };
      return validConfig(cfg) ? cfg : null;
    } catch (e) { return null; }
  }

  // ---------- GitHub Gist client (injected fetch; only api.github.com) ----------
  function describeError(status, headers, bodyMsg) {
    var remaining = headers && headers.get ? headers.get('x-ratelimit-remaining') : null;
    var reset = headers && headers.get ? headers.get('x-ratelimit-reset') : null;
    if (status === 0) return { kind: 'network', retry: true, message: 'Offline or GitHub unreachable — will retry.' };
    if (status === 401) return { kind: 'auth', retry: false, message: 'GitHub rejected the token (expired or revoked). Create a new token and reconnect.' };
    if (status === 403 || status === 429) {
      if (remaining === '0' || status === 429) {
        var wait = reset ? Math.max(30, Number(reset) - Math.floor(Date.now() / 1000)) : 300;
        return { kind: 'rate', retry: true, waitMs: Math.min(wait, 3600) * 1000, message: 'GitHub rate limit reached — will retry automatically.' };
      }
      return { kind: 'auth', retry: false, message: 'Token cannot use gists. It needs the “gist” scope (classic token).' };
    }
    if (status === 404) return { kind: 'notfound', retry: false, message: 'Sync gist not found (deleted, or this token cannot see it). Reconnect to create a new one.' };
    if (status >= 500) return { kind: 'server', retry: true, message: 'GitHub is having problems — will retry.' };
    return { kind: 'other', retry: false, message: 'GitHub error ' + status + (bodyMsg ? ': ' + String(bodyMsg).slice(0, 120) : '') };
  }

  function createGistClient(fetchFn, token) {
    async function call(method, path, opts) {
      opts = opts || {};
      var headers = {
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        Authorization: 'Bearer ' + token,
      };
      if (opts.body) headers['Content-Type'] = 'application/json';
      if (opts.etag) headers['If-None-Match'] = opts.etag;
      var res;
      try {
        res = await fetchFn(API_BASE + path, { method: method, headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
      } catch (e) {
        return { ok: false, status: 0, error: describeError(0) };
      }
      var etag = res.headers && res.headers.get ? res.headers.get('etag') : null;
      if (res.status === 304) return { ok: true, status: 304, notModified: true, etag: etag || opts.etag };
      var data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!res.ok) return { ok: false, status: res.status, error: describeError(res.status, res.headers, data && data.message) };
      return { ok: true, status: res.status, data: data, etag: etag };
    }

    return {
      // Find the user's secret sync gist (by description + file name), else null.
      async findGist() {
        for (var page = 1; page <= 10; page++) {
          var r = await call('GET', '/gists?per_page=100&page=' + page);
          if (!r.ok) return r;
          var list = Array.isArray(r.data) ? r.data : [];
          var hit = list.find(function (g) { return g && g.description === GIST_DESC && g.files && g.files[FILE_NAME]; });
          if (hit) return { ok: true, gist: hit };
          if (list.length < 100) break;
        }
        return { ok: true, gist: null };
      },
      async createGist(tracker) {
        var body = { description: GIST_DESC, public: false, files: {} };
        body.files[FILE_NAME] = { content: JSON.stringify(normalizeTracker(tracker)) };
        var r = await call('POST', '/gists', { body: body });
        return r.ok ? { ok: true, gist: r.data, etag: r.etag } : r;
      },
      // -> {ok, notModified?, tracker, etag} ; missing file = empty tracker.
      async getTracker(gistId, etag) {
        var r = await call('GET', '/gists/' + encodeURIComponent(gistId), { etag: etag });
        if (!r.ok || r.notModified) return r;
        var f = r.data && r.data.files && r.data.files[FILE_NAME];
        if (!f) return { ok: true, status: r.status, tracker: emptyTracker(), etag: r.etag };
        if (f.truncated) return { ok: false, status: 200, error: { kind: 'other', retry: false, message: 'Sync file is too large for the Gist API.' } };
        var parsed;
        try { parsed = JSON.parse(f.content); } catch (e) {
          return { ok: false, status: 200, error: { kind: 'corrupt', retry: false, message: 'Remote sync file is not valid JSON; refusing to overwrite it.' } };
        }
        return { ok: true, status: r.status, tracker: normalizeTracker(parsed), etag: r.etag };
      },
      async putTracker(gistId, tracker) {
        var body = { files: {} };
        body.files[FILE_NAME] = { content: JSON.stringify(normalizeTracker(tracker)) };
        var r = await call('PATCH', '/gists/' + encodeURIComponent(gistId), { body: body });
        return r.ok ? { ok: true, status: r.status, etag: r.etag } : r;
      },
      async deleteGist(gistId) { return call('DELETE', '/gists/' + encodeURIComponent(gistId)); },
    };
  }

  return {
    LEGACY_AT: LEGACY_AT, FILE_NAME: FILE_NAME, GIST_DESC: GIST_DESC,
    hashUrl: hashUrl, stableStringify: stableStringify, eventId: eventId,
    emptyTracker: emptyTracker, emptyView: emptyView, normalizeTracker: normalizeTracker,
    mergeTrackerState: mergeTrackerState, materialize: materialize,
    viewFromLegacy: viewFromLegacy, applyViewChanges: applyViewChanges,
    migrateLegacy: migrateLegacy, viewsEqual: viewsEqual,
    makeSetupFragment: makeSetupFragment, parseSetupFragment: parseSetupFragment,
    validConfig: validConfig, describeError: describeError, createGistClient: createGistClient,
  };
});

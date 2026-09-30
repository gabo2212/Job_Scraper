// Run: node --test tests/js   (also wrapped by tests/test_tracker_sync_js.py)
const test = require('node:test');
const assert = require('node:assert/strict');
const TS = require('../../tracker-sync.js');

const U1 = 'https://example.com/jobs/1';
const U2 = 'https://example.com/jobs/2';
const FAKE_TOKEN = 'mocktoken_FAKEFAKEFAKEFAKEFAKEFAKE0000';
const GIST_ID = 'abcdef0123456789abcdef0123456789';

function view(over) { return Object.assign(TS.emptyView(), over || {}); }
function edit(tracker, baseV, curV, now) { return TS.applyViewChanges(tracker, baseV, curV, now); }

test('legacy local format migrates losslessly and is older than any real edit', () => {
  const legacy = {
    triage: { [U1]: 'saved', [U2]: 'applied' },
    notes: { [U1]: 'call recruiter' },
    stars: { [U1]: 4 },
    timeline: { [U2]: [{ ts: 1000, text: 'applied online' }] },
    blockedCompanies: ['acme'],
  };
  const tr = TS.migrateLegacy(legacy);
  const v = TS.materialize(tr);
  assert.equal(v.triage[U1], 'saved');
  assert.equal(v.triage[U2], 'applied');
  assert.equal(v.notes[U1], 'call recruiter');
  assert.equal(v.stars[U1], 4);
  assert.equal(v.timeline[U2][0].text, 'applied online');
  assert.deepEqual(v.blockedCompanies, ['acme']);
  assert.equal(tr.jobs[TS.hashUrl(U1)].statusAt, TS.LEGACY_AT);
  // a real edit (remote says interview at t=500) beats legacy 'applied'
  const remote = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U2]: 'interview' } }), 500);
  assert.equal(TS.materialize(TS.mergeTrackerState(tr, remote)).triage[U2], 'interview');
});

test('the user scenario: 1 saved + 3 applied survive migration and push up unchanged', () => {
  const urls = [1, 2, 3, 4].map(n => `https://example.com/j/${n}`);
  const legacy = { triage: { [urls[0]]: 'saved', [urls[1]]: 'applied', [urls[2]]: 'applied', [urls[3]]: 'applied' } };
  const local = TS.migrateLegacy(legacy);
  const remoteEmpty = TS.emptyTracker();
  const merged = TS.mergeTrackerState(local, remoteEmpty);
  const v = TS.materialize(merged);
  assert.equal(Object.values(v.triage).filter(s => s === 'saved').length, 1);
  assert.equal(Object.values(v.triage).filter(s => s === 'applied').length, 3);
  assert.notEqual(TS.stableStringify(merged), TS.stableStringify(remoteEmpty)); // => gets pushed
});

test('concurrent edits on different fields from two devices both survive', () => {
  const base = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U1]: 'saved' } }), 100);
  const baseV = TS.materialize(base);
  const phone = edit(base, baseV, view({ triage: { [U1]: 'saved' }, notes: { [U1]: 'phone note' } }), 200);
  const desk = edit(base, baseV, view({ triage: { [U1]: 'applied' } }), 300);
  const m1 = TS.materialize(TS.mergeTrackerState(phone, desk));
  const m2 = TS.materialize(TS.mergeTrackerState(desk, phone));
  assert.deepEqual(m1, m2); // commutative
  assert.equal(m1.triage[U1], 'applied');
  assert.equal(m1.notes[U1], 'phone note');
});

test('same field edited on both devices: last writer wins, regardless of merge order', () => {
  const a = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U1]: 'saved' } }), 100);
  const b = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U1]: 'offer' } }), 200);
  assert.equal(TS.materialize(TS.mergeTrackerState(a, b)).triage[U1], 'offer');
  assert.equal(TS.materialize(TS.mergeTrackerState(b, a)).triage[U1], 'offer');
});

test('older remote does not clobber newer local; newer remote does clobber older local', () => {
  const older = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U1]: 'saved' } }), 100);
  const newer = edit(older, TS.materialize(older), view({ triage: { [U1]: 'applied' } }), 900);
  assert.equal(TS.materialize(TS.mergeTrackerState(newer, older)).triage[U1], 'applied');
  assert.equal(TS.materialize(TS.mergeTrackerState(older, newer)).triage[U1], 'applied');
});

test('deleting (un-saving) is a tombstone that beats an older remote value and propagates', () => {
  const saved = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U1]: 'saved' } }), 100);
  const cleared = edit(saved, TS.materialize(saved), TS.emptyView(), 500);
  const merged = TS.mergeTrackerState(saved, cleared); // "remote" still has old saved
  assert.equal(TS.materialize(merged).triage[U1], undefined);
  // and a stale device that re-saves at an OLDER time cannot resurrect it
  const stale = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U1]: 'saved' } }), 300);
  assert.equal(TS.materialize(TS.mergeTrackerState(cleared, stale)).triage[U1], undefined);
});

test('events: union by id from both devices; deletion tombstones propagate', () => {
  const e1 = { ts: 1000, text: 'phone screen' };
  const e2 = { ts: 2000, text: 'sent thank-you' };
  const a = edit(TS.emptyTracker(), TS.emptyView(), view({ timeline: { [U1]: [e1] } }), 1100);
  const b = edit(TS.emptyTracker(), TS.emptyView(), view({ timeline: { [U1]: [e2] } }), 2100);
  const merged = TS.mergeTrackerState(a, b);
  const tl = TS.materialize(merged).timeline[U1];
  assert.deepEqual(tl.map(e => e.text), ['phone screen', 'sent thank-you']);
  // device A deletes e1 (it had both after merging)
  const aView = TS.materialize(merged);
  const afterDel = edit(merged, aView, view({ timeline: { [U1]: [aView.timeline[U1][1]] } }), 3000);
  const back = TS.materialize(TS.mergeTrackerState(b, afterDel)).timeline[U1];
  assert.deepEqual(back.map(e => e.text), ['sent thank-you']);
  // re-merging the pre-delete copy never resurrects the deleted event
  assert.deepEqual(TS.materialize(TS.mergeTrackerState(afterDel, merged)).timeline[U1].map(e => e.text), ['sent thank-you']);
});

test('identical legacy events on two devices dedupe (deterministic ids)', () => {
  const legacy = { timeline: { [U1]: [{ ts: 5, text: 'applied' }] } };
  const d1 = TS.migrateLegacy(legacy), d2 = TS.migrateLegacy(legacy);
  assert.equal(TS.materialize(TS.mergeTrackerState(d1, d2)).timeline[U1].length, 1);
});

test('stale baseline only writes own changes (the stale-tab overwrite bug)', () => {
  // Tab A and tab B both loaded the same baseline. A marks job1 applied.
  const start = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U1]: 'saved' } }), 100);
  const baseV = TS.materialize(start);
  const afterA = edit(start, baseV, view({ triage: { [U1]: 'applied' } }), 200);
  // Tab B (stale, still thinks job1=saved) stars job2. Its current view has job1=saved (unchanged).
  const bCurrent = view({ triage: { [U1]: 'saved' }, stars: { [U2]: 3 } });
  const afterB = edit(afterA /* latest stored */, baseV /* stale baseline */, bCurrent, 300);
  const v = TS.materialize(afterB);
  assert.equal(v.triage[U1], 'applied'); // NOT reverted to saved
  assert.equal(v.stars[U2], 3);
});

test('merge is idempotent, commutative and associative', () => {
  const a = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U1]: 'saved' }, blockedCompanies: ['x'] }), 100);
  const b = edit(TS.emptyTracker(), TS.emptyView(), view({ triage: { [U1]: 'applied', [U2]: 'saved' }, notes: { [U2]: 'n' } }), 200);
  const c = edit(TS.emptyTracker(), TS.emptyView(), view({ stars: { [U2]: 5 }, timeline: { [U2]: [{ ts: 9, text: 't' }] } }), 300);
  const S = TS.stableStringify, M = TS.mergeTrackerState;
  assert.equal(S(M(a, a)), S(a));
  assert.equal(S(M(a, b)), S(M(b, a)));
  assert.equal(S(M(M(a, b), c)), S(M(a, M(b, c))));
  assert.equal(S(M(M(a, b), b)), S(M(a, b)));
});

test('blocked companies: LWW with unblock', () => {
  const on = edit(TS.emptyTracker(), TS.emptyView(), view({ blockedCompanies: ['acme'] }), 100);
  const off = edit(on, TS.materialize(on), TS.emptyView(), 200);
  assert.deepEqual(TS.materialize(TS.mergeTrackerState(on, off)).blockedCompanies, []);
  assert.deepEqual(TS.materialize(TS.mergeTrackerState(off, on)).blockedCompanies, []);
});

test('normalizeTracker drops garbage instead of trusting it', () => {
  const n = TS.normalizeTracker({ jobs: { a: 'x', b: { url: 5, status: 7, statusAt: 'NaN', events: { e: 1 } } }, blocked: { z: 3 } });
  assert.equal(Object.keys(n.blocked).length, 0);
  assert.equal(n.jobs.a, undefined);
  assert.equal(n.jobs.b.status, null);
  assert.deepEqual(TS.normalizeTracker(null), TS.emptyTracker());
});

test('setup fragment round-trips and rejects junk', () => {
  const frag = TS.makeSetupFragment({ gistId: GIST_ID, token: FAKE_TOKEN });
  assert.ok(frag.startsWith('#sync='));
  assert.ok(!/[+/=]/.test(frag.slice(6)));
  assert.deepEqual(TS.parseSetupFragment(frag), { gistId: GIST_ID, token: FAKE_TOKEN });
  assert.equal(TS.parseSetupFragment('#sync=@@@'), null);
  assert.equal(TS.parseSetupFragment('#other=1'), null);
  const bad = '#sync=' + Buffer.from(JSON.stringify({ gistId: '../../x', token: 'short' })).toString('base64');
  assert.equal(TS.parseSetupFragment(bad), null);
  assert.equal(TS.validConfig({ gistId: GIST_ID, token: FAKE_TOKEN }), true);
});


test('browser build (no Buffer, page-defined escape/unescape) round-trips the setup link', () => {
  // The dashboard defines its own global escape() for HTML; the module must not rely on the legacy one.
  const vm = require('node:vm'), fs = require('node:fs'), path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '../../tracker-sync.js'), 'utf8');
  const sandbox = { TextEncoder, TextDecoder, btoa, atob, JSON, Math, Date, Object, Array, String, Number, RegExp, Promise,
    escape: () => 'HTML-ESCAPE', unescape: () => 'HTML-UNESCAPE' };
  sandbox.self = sandbox;
  vm.runInNewContext(src, sandbox);
  const B = sandbox.TrackerSync;
  assert.ok(B, 'attached to global as TrackerSync');
  const frag = B.makeSetupFragment({ gistId: GIST_ID, token: FAKE_TOKEN });
  assert.equal(frag, TS.makeSetupFragment({ gistId: GIST_ID, token: FAKE_TOKEN }));   // same bytes as Node build
  assert.equal(B.parseSetupFragment(frag).token, FAKE_TOKEN);
});

// ---------- mocked Gist API ----------
function mockGistServer(opts) {
  opts = opts || {};
  const gists = new Map(); // id -> {description, content, etag, rev}
  const calls = [];
  const srv = {
    gists, calls,
    failNext: null, // {status, headers}
    async fetch(url, init) {
      const method = (init && init.method) || 'GET';
      const u = new URL(url);
      calls.push({ method, path: u.pathname + u.search, auth: init.headers.Authorization });
      const hdrs = (o) => ({ get: (k) => (o || {})[String(k).toLowerCase()] || null });
      const respond = (status, body, h) => ({ ok: status >= 200 && status < 300, status, headers: hdrs(h), json: async () => body });
      if (srv.failNext) { const f = srv.failNext; srv.failNext = null; return respond(f.status, { message: 'x' }, f.headers); }
      if (opts.offline) throw new TypeError('network down');
      if (init.headers.Authorization !== 'Bearer ' + FAKE_TOKEN) return respond(401, { message: 'Bad credentials' });
      if (method === 'GET' && u.pathname === '/gists') {
        const page = Number(u.searchParams.get('page') || 1);
        const all = [...gists.entries()].map(([id, g]) => ({ id, description: g.description, files: { [TS.FILE_NAME]: {} } }));
        const filler = (opts.fillerGists || []);
        const list = filler.concat(all).slice((page - 1) * 100, page * 100);
        return respond(200, list);
      }
      if (method === 'POST' && u.pathname === '/gists') {
        const b = JSON.parse(init.body);
        assert.equal(b.public, false, 'gist must be secret');
        const id = 'f'.repeat(32).slice(0, 31) + String(gists.size);
        gists.set(id, { description: b.description, content: b.files[TS.FILE_NAME].content, rev: 1 });
        return respond(201, { id, files: {} }, { etag: '"1"' });
      }
      const m = /^\/gists\/([^/]+)$/.exec(u.pathname);
      if (m && gists.has(m[1])) {
        const g = gists.get(m[1]);
        if (method === 'GET') {
          if (init.headers['If-None-Match'] === '"' + g.rev + '"') return respond(304, null, { etag: '"' + g.rev + '"' });
          return respond(200, { id: m[1], files: { [TS.FILE_NAME]: { content: g.content } } }, { etag: '"' + g.rev + '"' });
        }
        if (method === 'PATCH') {
          g.content = JSON.parse(init.body).files[TS.FILE_NAME].content; g.rev++;
          return respond(200, { id: m[1] }, { etag: '"' + g.rev + '"' });
        }
        if (method === 'DELETE') { gists.delete(m[1]); return respond(204, null); }
      }
      return respond(404, { message: 'Not Found' });
    },
  };
  return srv;
}

// One full device sync cycle exactly as the dashboard does it.
async function deviceSync(dev, client, gistId) {
  const r = await client.getTracker(gistId, dev.etag);
  if (!r.ok) return r;
  if (r.notModified && !dev.dirty) return { ok: true, skipped: true };
  const remote = r.notModified ? null : r.tracker;
  const merged = remote ? TS.mergeTrackerState(dev.tracker, remote) : dev.tracker;
  dev.tracker = merged;
  dev.dirty = false;
  if (!remote || TS.stableStringify(merged) !== TS.stableStringify(remote)) {
    const p = await client.putTracker(gistId, merged);
    if (!p.ok) return p;
    dev.etag = p.etag;
  } else dev.etag = r.etag;
  return { ok: true };
}

test('gist client: find-or-create is idempotent and uses a secret gist', async () => {
  const srv = mockGistServer();
  const client = TS.createGistClient(srv.fetch, FAKE_TOKEN);
  assert.equal((await client.findGist()).gist, null);
  const created = await client.createGist(TS.emptyTracker());
  assert.ok(created.ok);
  const found = await client.findGist();
  assert.equal(found.gist.id, created.gist.id);
  assert.equal(srv.gists.size, 1);
  assert.ok(srv.calls.every(c => !c.path.includes(FAKE_TOKEN)), 'token never appears in a URL');
});

test('gist client: finds the gist on a later page', async () => {
  const filler = Array.from({ length: 100 }, (_, i) => ({ id: 'a' + i, description: 'other', files: { x: {} } }));
  const srv = mockGistServer({ fillerGists: filler });
  const client = TS.createGistClient(srv.fetch, FAKE_TOKEN);
  const created = await client.createGist(TS.emptyTracker());
  const found = await client.findGist();
  assert.equal(found.gist.id, created.gist.id);
});

test('two devices converge through the gist without clobbering each other', async () => {
  const srv = mockGistServer();
  const client = TS.createGistClient(srv.fetch, FAKE_TOKEN);
  const { gist } = await client.createGist(TS.emptyTracker());
  const phone = { tracker: TS.emptyTracker(), etag: null };
  const desk = { tracker: TS.migrateLegacy({ triage: { [U1]: 'saved' } }), etag: null }; // legacy desktop data
  await deviceSync(desk, client, gist.id);            // desktop pushes legacy
  await deviceSync(phone, client, gist.id);           // phone pulls
  assert.equal(TS.materialize(phone.tracker).triage[U1], 'saved');
  // concurrent offline edits
  phone.tracker = edit(phone.tracker, TS.materialize(phone.tracker), view({ triage: { [U1]: 'saved' }, notes: { [U1]: 'from phone' } }), 1000);
  desk.tracker = edit(desk.tracker, TS.materialize(desk.tracker), view({ triage: { [U1]: 'applied' }, stars: { [U2]: 2 } }), 2000);
  phone.dirty = true; desk.dirty = true;
  await deviceSync(desk, client, gist.id);
  await deviceSync(phone, client, gist.id);           // phone merges desktop's newer status, keeps own note
  await deviceSync(desk, client, gist.id);            // desktop picks phone note
  const a = TS.materialize(desk.tracker), b = TS.materialize(phone.tracker);
  assert.deepEqual(a, b);
  assert.equal(a.triage[U1], 'applied');
  assert.equal(a.notes[U1], 'from phone');
  assert.equal(a.stars[U2], 2);
  // steady state: a no-change sync is a 304 and pushes nothing
  const before = srv.calls.filter(c => c.method === 'PATCH').length;
  await deviceSync(desk, client, gist.id);
  await deviceSync(desk, client, gist.id);
  assert.equal(srv.calls.filter(c => c.method === 'PATCH').length, before);
});

test('lost-update race (both push from stale pulls) self-heals on the next sync', async () => {
  const srv = mockGistServer();
  const client = TS.createGistClient(srv.fetch, FAKE_TOKEN);
  const { gist } = await client.createGist(TS.emptyTracker());
  const A = { tracker: TS.emptyTracker(), etag: null }, B = { tracker: TS.emptyTracker(), etag: null };
  A.tracker = edit(A.tracker, TS.emptyView(), view({ triage: { [U1]: 'applied' } }), 100);
  B.tracker = edit(B.tracker, TS.emptyView(), view({ triage: { [U2]: 'saved' } }), 200);
  // interleave: both read empty remote, then both write (B's write wins the race)
  const ra = await client.getTracker(gist.id), rb = await client.getTracker(gist.id);
  await client.putTracker(gist.id, TS.mergeTrackerState(A.tracker, ra.tracker));
  await client.putTracker(gist.id, TS.mergeTrackerState(B.tracker, rb.tracker));
  await deviceSync(A, client, gist.id); // A sees remote lacks U1 -> re-pushes merged
  await deviceSync(B, client, gist.id);
  const final = TS.materialize((await client.getTracker(gist.id)).tracker);
  assert.equal(final.triage[U1], 'applied');
  assert.equal(final.triage[U2], 'saved');
});

test('error mapping: 401, 403 missing scope, 403 rate limit, 404, 5xx, offline', async () => {
  const srv = mockGistServer();
  const c = TS.createGistClient(srv.fetch, 'wrongtoken_WRONGWRONGWRONGWRONG');
  const r401 = await c.findGist();
  assert.equal(r401.error.kind, 'auth'); assert.equal(r401.error.retry, false);
  const good = TS.createGistClient(srv.fetch, FAKE_TOKEN);
  srv.failNext = { status: 403, headers: { 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': String(Math.floor(Date.now() / 1000) + 120) } };
  const rl = await good.findGist();
  assert.equal(rl.error.kind, 'rate'); assert.equal(rl.error.retry, true); assert.ok(rl.error.waitMs >= 30000);
  srv.failNext = { status: 403, headers: {} };
  const scope = await good.findGist();
  assert.equal(scope.error.kind, 'auth'); assert.match(scope.error.message, /gist/);
  assert.equal((await good.getTracker('deadbeefdeadbeef')).error.kind, 'notfound');
  srv.failNext = { status: 502, headers: {} };
  assert.equal((await good.findGist()).error.kind, 'server');
  const off = TS.createGistClient(mockGistServer({ offline: true }).fetch, FAKE_TOKEN);
  const ro = await off.findGist();
  assert.equal(ro.error.kind, 'network'); assert.equal(ro.error.retry, true);
});

test('corrupt remote file is never overwritten', async () => {
  const srv = mockGistServer();
  const client = TS.createGistClient(srv.fetch, FAKE_TOKEN);
  const { gist } = await client.createGist(TS.emptyTracker());
  srv.gists.get(gist.id).content = '{not json';
  const r = await client.getTracker(gist.id);
  assert.equal(r.ok, false);
  assert.equal(r.error.kind, 'corrupt');
});

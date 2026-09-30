"""Manual browser verification for tab + device sync (NOT run in CI; needs Playwright).

  python -m http.server 8765      # in another terminal, from the repo root
  python tests/local/browser_verify_sync.py

Uses a MOCKED api.github.com via route interception and a fake token; never touches the real API.
"""
import json, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8765/triage.html"
import os
DOCS = Path(__file__).resolve().parents[2] / "docs"
CHANNEL = os.environ.get("PW_CHANNEL", "")   # e.g. PW_CHANNEL=msedge when Playwright browsers are not installed
FAKE = "mocktoken_" + "MOCKMOCKMOCKMOCKMOCKMOCK12"
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


class MockGist:
    def __init__(self):
        self.gists = {}
        self.calls = []

    def handle(self, route, request):
        url = request.url
        m = re.match(r"https://api\.github\.com(/[^?]*)(\?.*)?$", url)
        path = m.group(1)
        self.calls.append((request.method, path, request.headers.get("authorization", "")[:12], url))
        hdr = {"access-control-allow-origin": "*", "access-control-expose-headers": "etag, x-ratelimit-remaining, x-ratelimit-reset",
               "content-type": "application/json"}
        if request.method == "OPTIONS":
            route.fulfill(status=204, headers={**hdr, "access-control-allow-headers": "*", "access-control-allow-methods": "GET,POST,PATCH,DELETE"})
            return
        if request.headers.get("authorization") != "Bearer " + FAKE:
            route.fulfill(status=401, headers=hdr, body=json.dumps({"message": "Bad credentials"}))
            return
        if request.method == "GET" and path == "/gists":
            body = [{"id": i, "description": g["description"], "files": {"job-tracker-state.json": {}}} for i, g in self.gists.items()]
            route.fulfill(status=200, headers=hdr, body=json.dumps(body))
            return
        if request.method == "POST" and path == "/gists":
            b = json.loads(request.post_data)
            assert b["public"] is False
            gid = "a1b2c3d4e5f60718293a4b5c6d7e8f90"[: 32]
            self.gists[gid] = {"description": b["description"], "content": b["files"]["job-tracker-state.json"]["content"], "rev": 1}
            route.fulfill(status=201, headers={**hdr, "etag": '"1"'}, body=json.dumps({"id": gid}))
            return
        gm = re.match(r"/gists/([^/]+)$", path)
        if gm and gm.group(1) in self.gists:
            g = self.gists[gm.group(1)]
            if request.method == "GET":
                if request.headers.get("if-none-match") == f'"{g["rev"]}"':
                    route.fulfill(status=304, headers={**hdr, "etag": f'"{g["rev"]}"'}, body="")
                    return
                route.fulfill(status=200, headers={**hdr, "etag": f'"{g["rev"]}"'},
                              body=json.dumps({"id": gm.group(1), "files": {"job-tracker-state.json": {"content": g["content"]}}}))
                return
            if request.method == "PATCH":
                g["content"] = json.loads(request.post_data)["files"]["job-tracker-state.json"]["content"]
                g["rev"] += 1
                route.fulfill(status=200, headers={**hdr, "etag": f'"{g["rev"]}"'}, body=json.dumps({"id": gm.group(1)}))
                return
        route.fulfill(status=404, headers=hdr, body=json.dumps({"message": "Not Found"}))


def counts(page):
    return page.evaluate("""() => { const t = JSON.parse(localStorage.getItem('jobTriage:v3')||'{}').triage||{};
      const c = {}; Object.values(t).forEach(s => c[s]=(c[s]||0)+1); return c; }""")


def kpi_text(page):
    return page.evaluate("() => document.querySelector('#kpis') ? document.querySelector('#kpis').innerText.replace(/\\s+/g,' ') : ''")


def wait_ready(page):
    page.wait_for_selector(".job", timeout=20000)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(**({"channel": CHANNEL} if CHANNEL else {}))
        errors = []

        # ============ (a) two tabs, same browser context ============
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin="http://localhost:8765")
        A = ctx.new_page(); A.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        A.on("pageerror", lambda e: errors.append(str(e)))
        A.goto(BASE); wait_ready(A)
        urls = A.evaluate("() => [...document.querySelectorAll('.job')].slice(0,8).map(e => e.dataset.url)")
        # seed LEGACY state: 1 saved + 3 applied, no tracker key
        legacy = {"triage": {urls[0]: "saved", urls[1]: "applied", urls[2]: "applied", urls[3]: "applied"}, "notes": {urls[0]: "legacy note"}}
        A.evaluate("""([legacy]) => { const s = JSON.parse(localStorage.getItem('jobTriage:v3')||'{}'); Object.assign(s, legacy);
            localStorage.setItem('jobTriage:v3', JSON.stringify(s)); localStorage.removeItem('jobTriage:tracker:v1'); }""", [legacy])
        A.reload(); wait_ready(A)
        c = counts(A)
        check("legacy migration preserves 1 saved + 3 applied", c.get("saved") == 1 and c.get("applied") == 3, str(c))
        tr = A.evaluate("() => JSON.parse(localStorage.getItem('jobTriage:tracker:v1'))")
        check("tracker store created with 4+ entries", len(tr["jobs"]) >= 4)

        B = ctx.new_page(); B.on("pageerror", lambda e: errors.append(str(e)))
        B.goto(BASE); wait_ready(B)
        check("tab B sees migrated state", counts(B).get("applied") == 3)
        # A: click Applied on job 5 (find a card that is not already triaged)
        target = urls[5]
        A.bring_to_front()
        A.locator(f'.job[data-url="{target}"] .act[data-act="applied"]').first.click()
        t0 = time.time()
        B.wait_for_function("(u) => (JSON.parse(localStorage.getItem('jobTriage:tracker:v1'))||{jobs:{}}).jobs && Object.values(JSON.parse(localStorage.getItem('jobTriage:tracker:v1')).jobs).some(j => j.url===u && j.status==='applied')", arg=target, timeout=3000)
        B.wait_for_function("() => document.querySelector('#kpi-applied').textContent.trim() === '4'", timeout=3000)
        dt = time.time() - t0
        check("B updates without reload within ~1s (APPLIED 3->4)", dt < 1.5, f"{dt:.2f}s")
        # B (still has A's change adopted) edits a different job; A's change must survive
        B.bring_to_front()
        B.locator(f'.job[data-url="{urls[6]}"] .act[data-act="saved"]').first.click()
        A.wait_for_timeout(int(0.6*1000))
        A.bring_to_front(); A.wait_for_timeout(int(0.3*1000))
        ta = counts(A)
        check("A keeps its Applied AND sees B's Saved", ta.get("applied") == 4 and ta.get("saved") == 2, str(ta))
        # stale-tab overwrite: simulate B being stale by blocking its message listener -> directly edit storage from A then B writes
        B.evaluate("() => { window.__staleProbe = true; }")
        A.bring_to_front()
        A.locator(f'.job[data-url="{urls[7]}"] .act[data-act="interview"]').first.click()
        # B does NOT get time to process events: edit immediately through its UI
        B.bring_to_front()
        B.locator(f'.job[data-url="{urls[6]}"] .act[data-act="saved"]').first.click()   # un-save (toggle)
        A.wait_for_timeout(int(0.6*1000))
        final = A.evaluate("() => JSON.parse(localStorage.getItem('jobTriage:tracker:v1'))")
        stat = {j["url"]: j.get("status") for j in final["jobs"].values()}
        check("stale-ish write doesn't revert other tab's edit (interview kept)", stat.get(urls[7]) == "interview", str(stat.get(urls[7])))
        check("un-save from B propagated (tombstoned)", stat.get(urls[6]) in (None, ""), str(stat.get(urls[6])))
        A.screenshot(path=str(DOCS / "sync-tab-a.png"))

        # ============ (b) second "device" with MOCKED gist API ============
        mock = MockGist()
        dev1 = browser.new_context(viewport={"width": 1280, "height": 900})
        dev1.grant_permissions(["clipboard-read", "clipboard-write"], origin="http://localhost:8765")
        dev1.route("https://api.github.com/**", mock.handle)
        d1 = dev1.new_page(); d1.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        d1.on("pageerror", lambda e: errors.append(str(e)))
        d1.goto(BASE); wait_ready(d1)
        # seed same legacy data on device 1
        d1.evaluate("""([legacy]) => { const s = JSON.parse(localStorage.getItem('jobTriage:v3')||'{}'); Object.assign(s, legacy);
            localStorage.setItem('jobTriage:v3', JSON.stringify(s)); localStorage.removeItem('jobTriage:tracker:v1'); }""", [legacy])
        d1.reload(); wait_ready(d1)
        check("sync default is Off", "Off" in d1.inner_text("#sync-btn"))
        d1.click("#sync-btn")
        d1.screenshot(path=str(DOCS / "sync-panel-off.png"))
        d1.fill("#sync-token-input", FAKE)
        d1.click("#sync-connect")
        d1.wait_for_function("() => /Synced \\d\\d:\\d\\d/.test(document.querySelector('#sync-btn').innerText)", timeout=8000)
        check("device 1 connected + synced", True)
        check("token input cleared after connect", d1.input_value("#sync-token-input") == "")
        gid = next(iter(mock.gists))
        remote = json.loads(mock.gists[gid]["content"])
        rs = sorted(j.get("status") for j in remote["jobs"].values() if j.get("status"))
        check("initial push contains legacy 1 saved + 3 applied", rs == ["applied"] * 3 + ["saved"], str(rs))
        check("gist created secret (public:false asserted in mock)", True)
        cfg = d1.evaluate("() => localStorage.getItem('jobTriage:sync:v1')")
        check("token stored only in this device's localStorage", FAKE in cfg)
        d1.screenshot(path=str(DOCS / "sync-panel-connected.png"))
        d1.click("#sync-copy-link")
        link = d1.evaluate("() => navigator.clipboard.readText()")
        check("setup link has #sync= fragment", "#sync=" in link and link.startswith("http://localhost:8765/triage.html#sync="))
        d1.keyboard.press("Escape")
        # edit on d1 -> debounced push
        d1.locator(f'.job[data-url="{urls[4]}"] .act[data-act="offer"]').first.click()
        t1 = time.time()
        ok = False
        while time.time() - t1 < 12:
            remote = json.loads(mock.gists[gid]["content"])
            if any(j.get("status") == "offer" and j["url"] == urls[4] for j in remote["jobs"].values()):
                ok = True; break
            A.wait_for_timeout(int(0.25*1000))
        check("debounced push after edit (offer on remote)", ok, f"{time.time()-t1:.1f}s")
        print("D1:", d1.evaluate("() => [document.querySelector('#sync-btn-text').textContent, document.querySelector('#sync-status-line').textContent]"), [(m, p_) for m, p_, _, _ in mock.calls][-5:])

        # device 2: fresh context, import via setup link
        dev2 = browser.new_context(viewport={"width": 420, "height": 860}, is_mobile=True, has_touch=True)
        dev2.route("https://api.github.com/**", mock.handle)
        d2 = dev2.new_page(); d2.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        d2.on("pageerror", lambda e: errors.append(str(e)))
        d2.goto(link); wait_ready(d2)
        check("fragment cleared from URL after import", "#" not in d2.url and "sync=" not in d2.url, d2.url)
        try:
            d2.wait_for_function("() => /Synced \\d\\d:\\d\\d/.test(document.querySelector('#sync-btn').innerText)", timeout=12000)
        except Exception:
            print("D2 STATE:", d2.evaluate("() => [document.querySelector('#sync-btn-text').textContent, document.querySelector('#sync-status-line').textContent, (localStorage.getItem('jobTriage:sync:v1')||'').length]"), [(m, p_) for m, p_, _, _ in mock.calls][-5:])
            raise
        c2 = counts(d2)
        check("device 2 pulled the state (3 applied, 1 saved, 1 offer)", c2.get("applied") == 3 and c2.get("saved") == 1 and c2.get("offer") == 1, str(c2))
        stored = d2.evaluate("() => localStorage.getItem('jobTriage:sync:v1')")
        check("device 2 stored cfg", FAKE in stored)
        d2.screenshot(path=str(DOCS / "sync-device2-mobile.png"))
        # device 2 concurrent edit -> device 1 pulls via Sync now
        u2 = d2.evaluate("() => document.querySelector('.job:not(:has(.act.on))')?.dataset.url || document.querySelector('.job').dataset.url"); d2.locator(f'.job[data-url="{u2}"] .act[data-act="interview"]').first.click()
        A.wait_for_timeout(int(3.5*1000))
        d1.bring_to_front(); d1.click("#sync-btn"); d1.click("#sync-now")
        d1.wait_for_function("(u) => { const t = JSON.parse(localStorage.getItem('jobTriage:v3')||'{}').triage||{}; return t[u]==='interview'; }", arg=u2, timeout=15000)
        check("device 1 pulled device 2's interview via Sync now", True)
        d1.keyboard.press("Escape")
        # offline device 2: edit offline then come online
        dev2.set_offline(True)
        d2.locator(f'.job[data-url="{urls[3]}"] .act[data-act="dismiss"]').first.click() if d2.locator(f'.job[data-url="{urls[3]}"] .act[data-act="dismiss"]').count() else None
        A.wait_for_timeout(int(1*1000))
        dev2.set_offline(False)
        check("dashboard alive while offline (no crash)", d2.locator(".job").count() > 0)
        # error handling: bad token in fresh context shows clear message
        dev3 = browser.new_context(); dev3.route("https://api.github.com/**", mock.handle)
        d3 = dev3.new_page(); d3.goto(BASE); wait_ready(d3)
        d3.click("#sync-btn"); d3.fill("#sync-token-input", "wrongtoken_" + "WRONGWRONGWRONGWRONGWR")
        d3.click("#sync-connect"); A.wait_for_timeout(int(1*1000))
        msg = d3.inner_text("#sync-status-line")
        check("bad token -> clear message, still Off", "rejected" in msg.lower() and "Off" in d3.inner_text("#sync-btn"), msg)
        # network down with sync configured: page still works
        dev4 = browser.new_context()
        dev4.route("https://api.github.com/**", lambda r, q: r.abort())
        d4 = dev4.new_page(); d4.goto(BASE, wait_until="domcontentloaded")
        d4.evaluate("([t,g]) => localStorage.setItem('jobTriage:sync:v1', JSON.stringify({token:t, gistId:g}))", [FAKE, gid])
        d4.reload(); wait_ready(d4); A.wait_for_timeout(int(1.5*1000))
        check("network failure: dashboard works, status shows offline", "offline" in d4.inner_text("#sync-btn").lower() and d4.locator(".job").count() > 0, d4.inner_text("#sync-btn"))
        # token never in any URL sent
        check("token never in a request URL", all(FAKE not in u for (_, _, _, u) in mock.calls))
        check("only api.github.com gists endpoints called", all(pth.startswith("/gists") for (_, pth, _, _) in mock.calls))
        bad = [e for e in errors if "Failed to load resource" not in e]
        check("no console/page errors", not bad, "; ".join(bad)[:300])
        browser.close()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results)-len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

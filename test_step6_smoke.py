"""Step 6 smoke tests: routes, templates, SSE attributes."""
from __future__ import annotations

import os

os.environ.setdefault("MT_BASELINE_PATH", "baseline.yaml")

from fastapi.testclient import TestClient

from main import app

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail and not ok else ""))


with TestClient(app) as client:
    # --- GET / → redirect to /setup ---
    r = client.get("/", follow_redirects=False)
    check("GET / redirects", r.status_code == 307, f"got {r.status_code}")

    # --- GET /setup → 200 + setup form ---
    r = client.get("/setup")
    check("GET /setup 200", r.status_code == 200, f"got {r.status_code}")
    check("/setup has form", "hx-post" in r.text and "api/validate" in r.text)
    check("/setup has base layout", "MT Dataloader" in r.text)
    check("/setup has CSS link", "/static/style.css" in r.text)
    check("/setup has HTMX script", "htmx.org" in r.text)
    check("/setup has SSE extension", "htmx-ext-sse" in r.text or "sse.js" in r.text)

    # --- GET /runs → 200 + runs page ---
    r = client.get("/runs")
    check("GET /runs 200", r.status_code == 200, f"got {r.status_code}")
    check("/runs has load trigger", "hx-trigger" in r.text and "load" in r.text)
    check("/runs has credential inputs", 'name="api_key"' in r.text and 'name="org_id"' in r.text)

    # --- GET /api/runs → 200 + empty state ---
    r = client.get("/api/runs")
    check("GET /api/runs 200", r.status_code == 200, f"got {r.status_code}")
    check("/api/runs empty state", "No runs found" in r.text)

    # --- GET /static/style.css → 200 ---
    r = client.get("/static/style.css")
    check("GET /static/style.css 200", r.status_code == 200, f"got {r.status_code}")
    check("CSS has keyframes", "@keyframes spin" in r.text and "@keyframes pulse" in r.text)
    check("CSS has type-badge colors", "type-connection" in r.text and "type-entity" in r.text)

    # --- POST /api/execute without session → error ---
    r = client.post("/api/execute", data={"session_token": "invalid"})
    check("POST /api/execute expired session", r.status_code == 422, f"got {r.status_code}")
    check("error has back link", "Back to Setup" in r.text)

    # --- GET /api/execute/stream without session → SSE error+close ---
    r = client.get("/api/execute/stream?session_token=invalid")
    check(
        "GET /api/execute/stream expired → SSE stream",
        r.status_code == 200,
        f"got {r.status_code}",
    )
    check(
        "stream contains error event",
        "event: error" in r.text,
        "should have error event",
    )
    check(
        "stream contains close event",
        "event: close" in r.text,
        "should have close sentinel",
    )

    # --- GET /api/cleanup/stream without session → SSE error+close ---
    r = client.get("/api/cleanup/stream/invalid-token")
    check(
        "GET /api/cleanup/stream expired → SSE stream",
        r.status_code == 200,
        f"got {r.status_code}",
    )
    check(
        "cleanup stream contains error event",
        "event: error" in r.text,
    )

    # --- POST /api/cleanup with missing run → 404 ---
    r = client.post(
        "/api/cleanup/nonexistent",
        data={"api_key": "test", "org_id": "test"},
    )
    check("POST /api/cleanup missing run 404", r.status_code == 404, f"got {r.status_code}")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"Results: {passed}/{total} passed")
if passed < total:
    print("Failures:")
    for name, ok, detail in results:
        if not ok:
            print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
    exit(1)
else:
    print("All smoke tests passed!")

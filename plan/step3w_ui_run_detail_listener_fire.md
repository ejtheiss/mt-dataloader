# Step 3 — UI: Run Detail, Listener, Staged Fire, Navigation

## Scope

All UI templates, routes, fire endpoint, CSS, and wiring. After this step:

- `GET /runs/{run_id}` renders a four-tab run detail page (Config, Resources, Staged, Webhooks)
- `POST /api/runs/{run_id}/fire/{typed_ref:path}` fires a staged resource via the MT SDK
- `GET /listen` renders a standalone webhook listener with tunnel auto-detection
- Navigation updated: `/listen` tab in `base.html`, "Details" link on run cards, run detail link in `run_complete.html`
- `resource_row.html` handles `"staged"` status
- `_render_webhook_row` in `webhooks.py` replaced with Jinja2 template rendering
- `list_runs` glob filters out non-manifest JSON files
- CSS additions for webhook rows, staged rows, tab strip, fire button, tunnel banner

Everything builds on Steps 1 (webhooks backend) and 2 (staged model + engine).

---

## 3.1 Run Detail Page: `GET /runs/{run_id}`

### Route — `webhooks.py`

Add to `webhooks.py` (keeping all run-detail and webhook routes together):

```python
@router.get("/runs/{run_id}", include_in_schema=False)
async def run_detail_page(request: Request, run_id: str):
    """Four-tab run detail page: Config, Resources, Staged, Webhooks."""
    settings = request.app.state.settings
    runs_dir = Path(settings.runs_dir)

    manifest_path = runs_dir / f"{run_id}.json"
    if not manifest_path.exists():
        raise HTTPException(404, f"Run '{run_id}' not found")

    manifest = RunManifest.load(manifest_path)
    ensure_run_indexed(run_id, manifest)

    config_path = runs_dir / f"{run_id}_config.json"
    config_json = config_path.read_text("utf-8") if config_path.exists() else "{}"

    staged_path = runs_dir / f"{run_id}_staged.json"
    staged_payloads: dict[str, dict] = {}
    if staged_path.exists():
        try:
            staged_payloads = json.loads(staged_path.read_text("utf-8"))
        except json.JSONDecodeError:
            pass

    webhooks_path = runs_dir / f"{run_id}_webhooks.jsonl"
    webhook_history = load_webhooks(webhooks_path)

    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run_id": run_id,
            "manifest": manifest,
            "config_json": config_json,
            "staged_payloads": staged_payloads,
            "webhook_history": webhook_history,
        },
    )
```

### Why in `webhooks.py` not `main.py`

The run detail page is deeply tied to webhook data (SSE stream, JSONL history,
correlation index) and staged resources (fire endpoint, payload loading). Keeping
it in `webhooks.py` avoids circular imports and keeps `main.py` from growing
further (already ~890 lines).

### Template access

`webhooks.py` needs access to the Jinja2 `templates` instance. Import it from
`main.py`:

```python
from main import templates
```

**Circular import risk**: `main.py` imports `webhooks.py` (for the router).
`webhooks.py` importing `main.templates` creates a cycle. Solution: lazy import
inside the route functions, or pass `templates` via `app.state` at startup.

**Preferred: `app.state.templates`**. In `main.py` startup, assign:

```python
app.state.templates = templates
```

In `webhooks.py` routes, use:

```python
templates = request.app.state.templates
```

This avoids any circular import and follows the same pattern as
`request.app.state.settings`.

### Template imports in `webhooks.py`

`webhooks.py` also needs `RunManifest` and `HTTPException`:

```python
from fastapi import HTTPException  # add to existing import
from engine import RunManifest      # new import
```

### Template: `run_detail.html`

```
{% extends "base.html" %}
{% block nav_runs %}active{% endblock %}
{% block title %}Run {{ run_id }} — MT Dataloader{% endblock %}

{% block content %}
<div class="run-detail-container">
    <div class="run-detail-header">
        <h2>Run <code>{{ run_id }}</code></h2>
        <span class="run-status status-{{ manifest.status }}">
            {{ manifest.status }}
        </span>
    </div>

    <!-- Tab strip -->
    <div class="tab-strip">
        <button class="tab-btn active" data-tab="config">Config</button>
        <button class="tab-btn" data-tab="resources">
            Resources ({{ manifest.resources_created|length }})
        </button>
        <button class="tab-btn" data-tab="staged">
            Staged ({{ manifest.resources_staged|length }})
        </button>
        <button class="tab-btn" data-tab="webhooks">Webhooks</button>
    </div>

    <!-- Config tab -->
    <div class="tab-panel active" id="tab-config">
        <div class="config-viewer">
            <div class="config-actions">
                <button class="btn btn-sm btn-outline"
                        onclick="navigator.clipboard.writeText(
                            document.getElementById('config-json').textContent
                        )">Copy JSON</button>
            </div>
            <pre id="config-json" class="config-json">{{ config_json }}</pre>
        </div>
    </div>

    <!-- Resources tab -->
    <div class="tab-panel" id="tab-resources">
        {% if manifest.resources_created %}
        <table class="detail-table">
            <thead>
                <tr>
                    <th>Batch</th>
                    <th>Type</th>
                    <th>Ref</th>
                    <th>Created ID</th>
                    <th>Time</th>
                </tr>
            </thead>
            <tbody>
                {% for e in manifest.resources_created %}
                <tr>
                    <td><span class="batch-badge">B{{ e.batch }}</span></td>
                    <td><span class="type-badge type-{{ e.resource_type }}">
                        {{ e.resource_type }}</span></td>
                    <td>{{ e.typed_ref }}</td>
                    <td><code title="{{ e.created_id }}">
                        {{ e.created_id[:12] }}&hellip;</code></td>
                    <td>{{ e.created_at.split("T")[1][:8]
                           if "T" in e.created_at else e.created_at }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <p class="empty-state">No resources created.</p>
        {% endif %}

        {% if manifest.resources_failed %}
        <h4 style="margin-top: 16px; color: var(--danger);">Failed</h4>
        {% for f in manifest.resources_failed %}
        <div class="failed-entry">
            <code>{{ f.typed_ref }}</code>
            <span class="error-detail">{{ f.error }}</span>
        </div>
        {% endfor %}
        {% endif %}
    </div>

    <!-- Staged tab -->
    <div class="tab-panel" id="tab-staged">
        <!-- Credentials for fire endpoint -->
        <details class="fire-credentials" id="fire-creds">
            <summary>API Credentials (required to fire)</summary>
            <div style="margin-top: 8px;">
                <div class="form-row">
                    <div class="form-group">
                        <label for="fire-api-key">API Key</label>
                        <input type="password" id="fire-api-key" name="api_key"
                               placeholder="sk_test_..." autocomplete="off">
                    </div>
                    <div class="form-group">
                        <label for="fire-org-id">Org ID</label>
                        <input type="text" id="fire-org-id" name="org_id"
                               placeholder="org_..." autocomplete="off">
                    </div>
                </div>
            </div>
        </details>

        <div id="staged-list">
            {% if manifest.resources_staged %}
                {% for s in manifest.resources_staged %}
                {% set payload = staged_payloads.get(s.typed_ref, {}) %}
                {% include "partials/staged_row.html" %}
                {% endfor %}
            {% else %}
                <p class="empty-state">No staged resources in this run.</p>
            {% endif %}
        </div>
    </div>

    <!-- Webhooks tab (SSE-connected, no_replay=true) -->
    <div class="tab-panel" id="tab-webhooks"
         hx-ext="sse"
         sse-connect="/webhooks/stream?run_id={{ run_id }}&no_replay=true"
         sse-swap="webhook"
         hx-swap="beforeend"
         hx-target="#webhook-list">

        <div id="webhook-list">
            {% if webhook_history %}
                {% for wh in webhook_history %}
                {% include "partials/webhook_row.html" %}
                {% endfor %}
            {% else %}
                <p class="empty-state webhook-empty"
                   id="webhook-empty">No webhooks received yet.</p>
            {% endif %}
        </div>
    </div>
</div>

<script>
(function() {
    var tabs = document.querySelectorAll('.tab-btn');
    var panels = document.querySelectorAll('.tab-panel');
    tabs.forEach(function(btn) {
        btn.addEventListener('click', function() {
            tabs.forEach(function(t) { t.classList.remove('active'); });
            panels.forEach(function(p) { p.classList.remove('active'); });
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        });
    });
})();
</script>
{% endblock %}
```

### Why CSS show/hide tabs, NOT lazy-load

The Webhooks tab has an SSE connection (`sse-connect`). With lazy-load
(where the tab content is fetched on click), the SSE connection would be
destroyed when switching away and re-established when switching back.
CSS show/hide (`display: none` on inactive panels) keeps the SSE connection
alive across all tab switches. The same `hx-ext="sse"` + `sse-connect`
stays in the DOM.

### HTMX SSE wiring for webhooks

The Webhooks tab connects to `/webhooks/stream?run_id=X&no_replay=true`:
- `run_id` filter: only webhooks correlated to this run
- `no_replay=true`: skips ring buffer replay — historical webhooks are
  already server-rendered from JSONL via `{% include "partials/webhook_row.html" %}`

Live webhooks arrive as SSE events with `event: webhook`. The `sse-swap="webhook"`
attribute tells HTMX to swap the event data into `#webhook-list` via
`hx-swap="beforeend"` — new rows append at the bottom.

### Template context variables

| Variable | Source | Type |
|----------|--------|------|
| `run_id` | URL path param | `str` |
| `manifest` | `RunManifest.load()` | `RunManifest` |
| `config_json` | `{run_id}_config.json` file | `str` (pretty-printed) |
| `staged_payloads` | `{run_id}_staged.json` file | `dict[str, dict]` |
| `webhook_history` | `load_webhooks({run_id}_webhooks.jsonl)` | `list[dict]` |

---

## 3.2 Fire Endpoint: `POST /api/runs/{run_id}/fire/{typed_ref:path}`

### Route — `webhooks.py`

```python
@router.post("/api/runs/{run_id}/fire/{typed_ref:path}")
async def fire_staged(
    request: Request,
    run_id: str,
    typed_ref: str,
    api_key: str = Form(...),
    org_id: str = Form(...),
):
    """Fire a staged resource — sends the resolved payload to the MT API."""
    settings = request.app.state.settings
    runs_dir = Path(settings.runs_dir)

    # Load staged payloads
    staged_path = runs_dir / f"{run_id}_staged.json"
    if not staged_path.exists():
        raise HTTPException(404, "No staged payloads for this run")

    staged_payloads = json.loads(staged_path.read_text("utf-8"))
    if typed_ref not in staged_payloads:
        raise HTTPException(404, f"Staged payload not found: {typed_ref}")

    resolved = staged_payloads[typed_ref]
    resource_type = typed_ref.split(".")[0]

    async with AsyncModernTreasury(
        api_key=api_key, organization_id=org_id
    ) as client:
        result = await _fire_one(
            client, resource_type, resolved,
            idempotency_key=f"{run_id}:staged:{typed_ref}",
        )

    # Update manifest: move from staged to created
    manifest_path = runs_dir / f"{run_id}.json"
    manifest = RunManifest.load(manifest_path)
    manifest.resources_created.append(
        ManifestEntry(
            batch=-1,
            resource_type=resource_type,
            typed_ref=typed_ref,
            created_id=result["created_id"],
            created_at=_now_iso(),
            deletable=DELETABILITY.get(resource_type, False),
        )
    )
    manifest.resources_staged = [
        s for s in manifest.resources_staged if s.typed_ref != typed_ref
    ]
    manifest.write(settings.runs_dir)

    # Remove from staged payloads file
    del staged_payloads[typed_ref]
    if staged_payloads:
        staged_path.write_text(
            json.dumps(staged_payloads, indent=2, default=str), "utf-8"
        )
    else:
        staged_path.unlink(missing_ok=True)

    # Index for webhook correlation
    index_resource(run_id, result["created_id"], typed_ref)
    for child_key, child_id in result.get("child_refs", {}).items():
        index_resource(run_id, child_id, f"{typed_ref}.{child_key}")

    # Return updated staged row partial
    templates = request.app.state.templates
    html = templates.get_template("partials/staged_row_fired.html").render(
        s_typed_ref=typed_ref,
        resource_type=resource_type,
        created_id=result["created_id"],
        child_refs=result.get("child_refs", {}),
    )
    return HTMLResponse(html)
```

### Fire dispatch table

```python
from handlers import DELETABILITY, _poll_ipd_status

_FIRE_DISPATCH: dict[str, Callable] = {
    "payment_order":           _fire_payment_order,
    "expected_payment":        _fire_expected_payment,
    "ledger_transaction":      _fire_ledger_transaction,
    "incoming_payment_detail": _fire_incoming_payment_detail,
}


async def _fire_one(
    client: AsyncModernTreasury,
    resource_type: str,
    resolved: dict,
    *,
    idempotency_key: str,
) -> dict:
    """Dispatch a staged resource to the correct SDK method.

    Returns dict with 'created_id' and optional 'child_refs'.
    """
    handler = _FIRE_DISPATCH.get(resource_type)
    if not handler:
        raise HTTPException(400, f"Unsupported staged type: {resource_type}")
    return await handler(client, resolved, idempotency_key=idempotency_key)


async def _fire_payment_order(
    client: AsyncModernTreasury, resolved: dict, *, idempotency_key: str,
) -> dict:
    result = await client.payment_orders.create(
        **resolved, idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_expected_payment(
    client: AsyncModernTreasury, resolved: dict, *, idempotency_key: str,
) -> dict:
    result = await client.expected_payments.create(
        **resolved, idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_ledger_transaction(
    client: AsyncModernTreasury, resolved: dict, *, idempotency_key: str,
) -> dict:
    result = await client.ledger_transactions.create(
        **resolved, idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_incoming_payment_detail(
    client: AsyncModernTreasury, resolved: dict, *, idempotency_key: str,
) -> dict:
    result = await client.incoming_payment_details.create_async(
        **resolved, idempotency_key=idempotency_key,
    )

    # Poll for completion (reuse tenacity pattern from handlers.py)
    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_delay(30),
        retry=retry_if_result(lambda r: r.status != "completed"),
    )
    async def _poll():
        return await client.incoming_payment_details.retrieve(result.id)

    try:
        ipd = await _poll()
    except RetryError as e:
        last = e.last_attempt.result()
        raise HTTPException(
            504, f"IPD did not complete within 30s (status: {last.status})"
        )

    child_refs: dict[str, str] = {}
    if ipd.transaction_id:
        child_refs["transaction"] = ipd.transaction_id
    if ipd.ledger_transaction_id:
        child_refs["ledger_transaction"] = ipd.ledger_transaction_id

    return {"created_id": result.id, "child_refs": child_refs}
```

### Why `_fire_incoming_payment_detail` re-implements polling

The existing `_poll_ipd_status()` in `handlers.py` takes an `emit_sse`
callback and emits `"waiting"` events. The fire endpoint doesn't have an SSE
stream — it's a synchronous POST that returns an HTMX partial. Re-implementing
a simplified poll (without SSE callbacks) is cleaner than making `emit_sse`
optional in the handler's poll function and avoids coupling the fire endpoint
to the engine's SSE plumbing.

### Why `batch=-1` for fired resources

Staged resources weren't part of a regular batch. Using `-1` distinguishes
them from batch-0+ resources in the manifest. The UI can display "Fired"
instead of "B-1" for these entries.

### Idempotency key: `{run_id}:staged:{typed_ref}`

Distinct from the engine's `{run_id}:{typed_ref}` pattern. Prevents
conflicts if a resource is somehow created by both the engine (shouldn't
happen) and the fire endpoint.

### Credentials from form fields

Same pattern as cleanup (`main.py` lines 736-742): `api_key: str = Form(...)`,
`org_id: str = Form(...)`. The staged tab has credential inputs that are
included via `hx-include` on the fire button.

The `_creds.save()` and `_creds.restore()` logic in `base.html` (lines 19-40)
automatically persists credentials to `sessionStorage` across HTMX requests,
so credentials entered on the setup page are available on the run detail page.

### Imports needed in `webhooks.py`

```python
from fastapi import Form, HTTPException                # add to existing
from fastapi.responses import HTMLResponse             # new
from tenacity import RetryError, retry, retry_if_result, stop_after_delay, wait_exponential
from handlers import DELETABILITY, _poll_ipd_status    # new (DELETABILITY for manifest)
from engine import RunManifest, _now_iso               # add _now_iso
from models import ManifestEntry                       # for fired entry
```

### `_now_iso` export

Currently `_now_iso()` in `engine.py` is module-private (no `__all__`
listing). Add it to `__all__` or import directly. Since it's a trivial
one-liner, importing directly is fine — Python doesn't enforce `__all__`
for imports, only for `from module import *`.

---

## 3.3 Standalone Listener Page: `GET /listen`

### Route — `webhooks.py`

```python
@router.get("/listen", include_in_schema=False)
async def listen_page(request: Request):
    """Standalone webhook listener with tunnel auto-detection."""
    tunnel_url = await _detect_tunnel()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "listen.html",
        {
            "tunnel_url": tunnel_url,
            "webhook_path": "/webhooks/mt",
        },
    )
```

### Tunnel auto-detection

Ngrok exposes a local API at `http://127.0.0.1:4040/api/tunnels`. Probe it
to find a public HTTPS URL:

```python
async def _detect_tunnel() -> str | None:
    """Probe ngrok local API for a public tunnel URL.

    Returns the public HTTPS URL or None if ngrok isn't running.
    """
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://127.0.0.1:4040/api/tunnels")
            if resp.status_code == 200:
                data = resp.json()
                for tunnel in data.get("tunnels", []):
                    public_url = tunnel.get("public_url", "")
                    if public_url.startswith("https://"):
                        return public_url
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        pass
    return None
```

`httpx` is already imported in `webhooks.py` (it's in requirements.txt,
pinned in Step 1). Add the import if not present:

```python
import httpx
```

### Template: `listen.html`

```
{% extends "base.html" %}
{% block nav_listen %}active{% endblock %}
{% block title %}Webhook Listener — MT Dataloader{% endblock %}

{% block content %}
<div class="listen-container">
    <h2>Webhook Listener</h2>

    <!-- Tunnel status banner -->
    {% if tunnel_url %}
    <div class="tunnel-banner tunnel-ok">
        <strong>Tunnel detected:</strong>
        <code>{{ tunnel_url }}{{ webhook_path }}</code>
        <button class="btn btn-sm btn-outline"
                onclick="navigator.clipboard.writeText(
                    '{{ tunnel_url }}{{ webhook_path }}'
                )">Copy</button>
    </div>
    {% else %}
    <div class="tunnel-banner tunnel-warn">
        <strong>No tunnel detected.</strong>
        Start ngrok to receive webhooks from MT:
        <code>ngrok http 8000</code>
    </div>
    {% endif %}

    <p class="text-muted">
        Configure this URL in
        <a href="https://app.moderntreasury.com/developers/webhooks"
           target="_blank" rel="noopener">MT Dashboard &rarr; Webhooks</a>.
    </p>

    <!-- Test webhook button -->
    <div class="listen-actions">
        <button class="btn btn-sm btn-outline"
                hx-post="/webhooks/mt"
                hx-headers='{"X-Topic": "test", "X-Webhook-ID": "test-manual"}'
                hx-vals='{"event": "test", "data": {"id": "test-manual", "object": "test"}}'
                hx-swap="none">
            Send Test Webhook
        </button>
    </div>

    <!-- Live webhook feed via SSE (with replay for initial population) -->
    <div class="webhook-feed"
         hx-ext="sse"
         sse-connect="/webhooks/stream"
         sse-swap="webhook"
         hx-swap="beforeend"
         hx-target="#live-webhooks">

        <h3>Live Feed</h3>
        <div id="live-webhooks">
            <p class="empty-state" id="webhook-empty">
                Waiting for webhooks&hellip;
            </p>
        </div>
    </div>
</div>
{% endblock %}
```

### Why no `no_replay` on the listener SSE

The standalone listener has no server-rendered history (unlike the run
detail page). SSE replay from the ring buffer provides initial content so
the page isn't empty on load. The `no_replay` param is omitted, defaulting
to `False`.

### Test webhook button

Uses HTMX to POST to `/webhooks/mt` with synthetic headers and body. The
webhook receiver processes it normally — dedup, ring buffer, SSE fan-out.
The `hx-swap="none"` means the button click doesn't update any DOM element
directly; the webhook arrives via the SSE stream instead.

**Note**: The HTMX `hx-headers` approach for `X-Topic` and `X-Webhook-ID`
may not work because HTMX sends the body as form-encoded, not JSON. The
webhook receiver expects `json.loads()`. **Alternative**: Use a small
JavaScript function to send a proper `fetch()` POST with JSON body and
custom headers.

```html
<button class="btn btn-sm btn-outline"
        onclick="(function(){
            fetch('/webhooks/mt', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Topic': 'test',
                    'X-Webhook-ID': 'test-' + Date.now()
                },
                body: JSON.stringify({
                    event: 'test',
                    data: {id: 'test-' + Date.now(), object: 'test'}
                })
            });
        })()">
    Send Test Webhook
</button>
```

Using `Date.now()` in the webhook ID ensures each click produces a unique
webhook (passes dedup). The receiver processes it, adds to ring buffer, and
pushes via SSE.

---

## 3.4 Webhook Row Partial: `partials/webhook_row.html`

### Template

```html
<div class="webhook-row" id="wh-{{ wh.webhook_id if wh.webhook_id is string else wh.get('webhook_id', '') }}">
    <span class="wh-time">
        {% set ts = (wh.received_at if wh.received_at is string else wh.get('received_at', '')) %}
        {{ ts.split("T")[1][:8] if "T" in ts else ts }}
    </span>
    <span class="wh-event">{{ wh.event_type if wh.event_type is string else wh.get('event_type', '') }}</span>
    <span class="wh-ref">
        {% set ref = wh.typed_ref if wh.typed_ref is string else wh.get('typed_ref') %}
        {% set rid = wh.resource_id if wh.resource_id is string else wh.get('resource_id', '') %}
        {{ ref if ref else (rid[:12] + '...' if rid else '?') }}
    </span>
    {% set corr_run = wh.run_id if wh.run_id is string else wh.get('run_id') %}
    {% if corr_run %}
    <a href="/runs/{{ corr_run }}" class="wh-run-link" title="View run">
        {{ corr_run[:16] }}
    </a>
    {% endif %}
</div>
```

### Why dual access patterns (`wh.attr` and `wh.get(...)`)

The webhook row template is used in two contexts:
1. **Server-rendered from JSONL** (`webhook_history`): each `wh` is a `dict`
   from `json.loads()`. Access via `wh.get('key')` or `wh['key']`.
2. **SSE push** (from `_render_webhook_row` in `webhooks.py`): each `wh` is
   a `WebhookEntry` dataclass. Access via `wh.attr`.

Jinja2's attribute access (`wh.something`) tries both `wh.something` and
`wh['something']` automatically. So `wh.event_type` works for both dataclass
attributes and dict keys. The template can use a single access pattern:

```html
{{ wh.event_type }}
```

This works because Jinja2's `Environment.getattr` resolves both dict keys
and object attributes. **Simplified template** (preferred):

```html
<div class="webhook-row" id="wh-{{ wh.webhook_id }}">
    <span class="wh-time">
        {{ wh.received_at.split("T")[1][:8]
           if "T" in wh.received_at else wh.received_at }}
    </span>
    <span class="wh-event">{{ wh.event_type }}</span>
    <span class="wh-ref">
        {{ wh.typed_ref or (wh.resource_id[:12] + '...')
           if wh.resource_id else '?' }}
    </span>
    {% if wh.run_id %}
    <a href="/runs/{{ wh.run_id }}" class="wh-run-link">{{ wh.run_id[:16] }}</a>
    {% endif %}
</div>
```

### Update `_render_webhook_row` in `webhooks.py`

Replace the inline HTML rendering with Jinja2 template rendering:

```python
def _render_webhook_row(entry: WebhookEntry) -> str:
    """Render a webhook entry as an HTML snippet for SSE push."""
    from fastapi.templating import Jinja2Templates
    import os

    # Use the same templates instance via a lazy import
    templates = Jinja2Templates(directory="templates")
    return templates.get_template("partials/webhook_row.html").render(wh=entry)
```

**Better approach**: Cache the template at module level to avoid re-creating
`Jinja2Templates` per call:

```python
from jinja2 import Environment, FileSystemLoader

_jinja_env: Environment | None = None

def _get_jinja_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader("templates"),
            autoescape=True,
        )
    return _jinja_env

def _render_webhook_row(entry: WebhookEntry) -> str:
    """Render a webhook entry as an HTML snippet for SSE push."""
    tmpl = _get_jinja_env().get_template("partials/webhook_row.html")
    return tmpl.render(wh=entry)
```

This avoids depending on `app.state.templates` (which isn't available in
the module-level webhook SSE fan-out code) and creates a lightweight Jinja2
environment once.

---

## 3.5 Staged Row Partial: `partials/staged_row.html`

### Template (before firing)

```html
{% set row_id = "staged-" + s.typed_ref|replace(".", "-")|replace("[", "_")|replace("]", "") %}
{% set resource_type = s.typed_ref.split(".")[0] %}
{% set ref_name = s.typed_ref.split(".", 1)[1] if "." in s.typed_ref else s.typed_ref %}

<div id="{{ row_id }}" class="staged-row">
    <span class="type-badge type-{{ resource_type }}">{{ resource_type }}</span>
    <span class="ref-name">{{ ref_name }}</span>
    <span class="staged-at">
        {{ s.staged_at.split("T")[1][:8]
           if "T" in s.staged_at else s.staged_at }}
    </span>

    {% if payload %}
    <details class="staged-payload">
        <summary>Payload</summary>
        <pre>{{ payload|tojson(indent=2) }}</pre>
    </details>
    {% endif %}

    <button class="btn btn-sm btn-primary btn-fire"
            hx-post="/api/runs/{{ run_id }}/fire/{{ s.typed_ref }}"
            hx-target="#{{ row_id }}"
            hx-swap="outerHTML"
            hx-include="#fire-creds [name=api_key], #fire-creds [name=org_id]"
            hx-disabled-elt="this"
            hx-indicator="#{{ row_id }}-spinner">
        Fire
        <span id="{{ row_id }}-spinner" class="htmx-indicator spinner-sm"></span>
    </button>
</div>
```

### Template (after firing): `partials/staged_row_fired.html`

```html
{% set row_id = "staged-" + s_typed_ref|replace(".", "-")|replace("[", "_")|replace("]", "") %}

<div id="{{ row_id }}" class="staged-row fired">
    <span class="type-badge type-{{ resource_type }}">{{ resource_type }}</span>
    <span class="ref-name">{{ s_typed_ref.split(".", 1)[1] }}</span>
    <span class="status-indicator">
        <span class="status-dot created"></span>
        <span class="status-text">Fired</span>
    </span>
    <code class="created-id" title="{{ created_id }}">
        {{ created_id[:12] }}&hellip;
    </code>
    {% if child_refs %}
    <span class="child-refs">
        {% for key, val in child_refs.items() %}
        <span class="child-ref" title="{{ val }}">+{{ key }}</span>
        {% endfor %}
    </span>
    {% endif %}
</div>
```

The fire endpoint returns this partial, and HTMX swaps it in place of the
original staged row (`hx-target="#staged-..."`, `hx-swap="outerHTML"`).

### Context variables

**`staged_row.html`:**

| Variable | Source | Type |
|----------|--------|------|
| `s` | `manifest.resources_staged[i]` | `StagedEntry` |
| `payload` | `staged_payloads.get(s.typed_ref, {})` | `dict` |
| `run_id` | Parent template context | `str` |

**`staged_row_fired.html`:**

| Variable | Source | Type |
|----------|--------|------|
| `s_typed_ref` | Fire endpoint response | `str` |
| `resource_type` | Extracted from `typed_ref` | `str` |
| `created_id` | SDK response | `str` |
| `child_refs` | SDK response (IPD only) | `dict[str, str]` |

---

## 3.6 Resource Row: Staged Status

### Change to `partials/resource_row.html`

Add a new `{% elif status == "staged" %}` block after the `"waiting"` block:

```html
    {% elif status == "staged" %}
        <span class="status-indicator">
            <span class="status-dot staged"></span>
            <span class="status-text">Staged</span>
        </span>
```

### Where `status == "staged"` comes from

In `main.py` `_make_emit_sse()`, the SSE events render `resource_row.html`
with a `status` variable. The engine emits `await emit_sse("staged", typed_ref, {})`
(engine.py, after the staged skip). The `_make_emit_sse` function maps the
event name to the `status` parameter:

```python
# Current _make_emit_sse (main.py):
async def emit_sse(event: str, typed_ref: str, data: dict) -> None:
    html = templates.get_template("partials/resource_row.html").render(
        ref=typed_ref, status=event, **data
    )
    await queue.put(ServerSentEvent(data=html, event=event))
```

So `event="staged"` → `status="staged"` → the new block renders.

### CSS for staged status dot

```css
.status-dot.staged {
    background-color: var(--status-staged, #6c757d);
}
```

Add `--status-staged` to `:root` CSS variables.

---

## 3.7 Navigation Updates

### 3.7a. `/listen` tab in `base.html`

**Current nav** (lines 46-49):

```html
<nav>
    <a href="/setup" class="nav-tab {% block nav_setup %}{% endblock %}">Setup</a>
    <a href="/runs" class="nav-tab {% block nav_runs %}{% endblock %}">Runs</a>
</nav>
```

**Change**: Add a third tab:

```html
<nav>
    <a href="/setup" class="nav-tab {% block nav_setup %}{% endblock %}">Setup</a>
    <a href="/runs" class="nav-tab {% block nav_runs %}{% endblock %}">Runs</a>
    <a href="/listen" class="nav-tab {% block nav_listen %}{% endblock %}">Listener</a>
</nav>
```

### 3.7b. "Details" link on run cards in `runs.html`

**Current run-actions** (lines 15-24):

```html
<div class="run-actions">
    <button class="btn btn-sm btn-danger" ...>Cleanup</button>
</div>
```

**Change**: Add a Details link before the Cleanup button:

```html
<div class="run-actions">
    <a href="/runs/{{ m.run_id }}" class="btn btn-sm btn-outline">Details</a>
    <button class="btn btn-sm btn-danger" ...>Cleanup</button>
</div>
```

Also add staged count to run card metadata if applicable:

```html
{% if m.resources_staged %}
<span class="staged-count">{{ m.resources_staged|length }} staged</span>
{% endif %}
```

### 3.7c. Run detail link in `run_complete.html`

**Current actions** (lines 10-13):

```html
<div class="run-actions" style="margin-top: 12px;">
    <a href="/runs" class="btn btn-secondary">View Runs</a>
    <a href="/setup" class="btn btn-primary">New Run</a>
</div>
```

**Change**: Add Details link:

```html
<div class="run-actions" style="margin-top: 12px;">
    <a href="/runs/{{ run_id }}" class="btn btn-outline">Details</a>
    <a href="/runs" class="btn btn-secondary">View Runs</a>
    <a href="/setup" class="btn btn-primary">New Run</a>
</div>
```

---

## 3.8 Fix `list_runs` Glob

### Problem

`main.py` line 721:

```python
for path in sorted(runs_dir.glob("*.json"), reverse=True):
```

This matches `{run_id}.json` (manifests), but also `{run_id}_config.json`
and `{run_id}_staged.json`. The latter two fail `RunManifest.load()` and
produce warning logs:

```
WARNING  Failed to load manifest  path=runs/20260320_ab12cd34_config.json ...
```

### Fix

Filter to manifest files only. Manifest filenames match the pattern
`YYYYMMDDTHHMMSS_<hex>.json` — no underscore suffix. The simplest filter:

```python
for path in sorted(runs_dir.glob("*.json"), reverse=True):
    if "_config.json" in path.name or "_staged.json" in path.name:
        continue
    try:
        manifests.append(RunManifest.load(path))
    except Exception as e:
        ...
```

Alternative: use a more specific glob `[0-9]*.json` — but this still
matches `_config.json` and `_staged.json` since the run_id starts with a
digit. The suffix filter is clearest.

---

## 3.9 Replace `_render_webhook_row` with Jinja2

### Current (inline HTML in `webhooks.py`)

```python
def _render_webhook_row(entry: WebhookEntry) -> str:
    time_str = (...)
    ref_display = (...)
    return (
        f'<div class="webhook-row" id="wh-{entry.webhook_id}">'
        ...
    )
```

### New (Jinja2 template rendering)

```python
from jinja2 import Environment, FileSystemLoader

_jinja_env: Environment | None = None


def _get_jinja_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader("templates"),
            autoescape=True,
        )
    return _jinja_env


def _render_webhook_row(entry: WebhookEntry) -> str:
    tmpl = _get_jinja_env().get_template("partials/webhook_row.html")
    return tmpl.render(wh=entry)
```

### Why a separate `_jinja_env` instead of `app.state.templates`

`_render_webhook_row` is called from `_fanout()` → `webhook_stream()` SSE
generator. The SSE generator runs in a background task — it doesn't have
access to `request.app.state` after the initial connection. A module-level
Jinja2 environment avoids this limitation.

The `autoescape=True` setting matches FastAPI's `Jinja2Templates` default.

---

## 3.10 CSS Additions

### New CSS variables

```css
:root {
    /* ... existing ... */
    --status-staged: #6c757d;
    --status-fired: #198754;
    --tunnel-ok: #d1e7dd;
    --tunnel-warn: #fff3cd;
}
```

### Tab strip

```css
.tab-strip {
    display: flex;
    gap: 0;
    border-bottom: 2px solid var(--border);
    margin-bottom: 16px;
}

.tab-btn {
    background: none;
    border: none;
    padding: 8px 16px;
    cursor: pointer;
    color: var(--text-muted);
    font-size: 0.9rem;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    transition: color 0.15s, border-color 0.15s;
}

.tab-btn:hover {
    color: var(--text);
}

.tab-btn.active {
    color: var(--primary);
    border-bottom-color: var(--primary);
    font-weight: 600;
}

.tab-panel {
    display: none;
}

.tab-panel.active {
    display: block;
}
```

### Run detail container

```css
.run-detail-container {
    max-width: 960px;
    margin: 0 auto;
    padding: 24px;
}

.run-detail-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
}

.run-detail-header h2 {
    margin: 0;
}
```

### Config viewer

```css
.config-viewer {
    position: relative;
}

.config-actions {
    position: absolute;
    top: 8px;
    right: 8px;
}

.config-json {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px;
    overflow-x: auto;
    font-size: 0.85rem;
    max-height: 500px;
    overflow-y: auto;
}
```

### Detail table (resources tab)

```css
.detail-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
}

.detail-table th,
.detail-table td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    text-align: left;
}

.detail-table th {
    background: var(--bg);
    font-weight: 600;
    color: var(--text-muted);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
}
```

### Staged rows

```css
.staged-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 8px;
    background: var(--surface);
}

.staged-row.fired {
    opacity: 0.7;
    border-color: var(--status-created);
}

.staged-payload {
    flex: 1;
}

.staged-payload pre {
    font-size: 0.8rem;
    max-height: 200px;
    overflow-y: auto;
    background: var(--bg);
    padding: 8px;
    border-radius: 4px;
    margin-top: 4px;
}

.btn-fire {
    white-space: nowrap;
}

.fire-credentials {
    margin-bottom: 16px;
    padding: 12px;
    background: var(--bg);
    border-radius: var(--radius);
}
```

### Webhook rows

```css
.webhook-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 12px;
    border-bottom: 1px solid var(--border);
    font-size: 0.85rem;
}

.wh-time {
    color: var(--text-muted);
    font-family: monospace;
    font-size: 0.8rem;
    min-width: 64px;
}

.wh-event {
    font-weight: 500;
    min-width: 180px;
}

.wh-ref {
    color: var(--text-muted);
    flex: 1;
}

.wh-run-link {
    font-size: 0.8rem;
    color: var(--primary);
}
```

### Tunnel banner

```css
.tunnel-banner {
    padding: 12px 16px;
    border-radius: var(--radius);
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}

.tunnel-ok {
    background: var(--tunnel-ok);
    border: 1px solid #a3cfbb;
}

.tunnel-warn {
    background: var(--tunnel-warn);
    border: 1px solid #ffc107;
}
```

### Listen container

```css
.listen-container {
    max-width: 800px;
    margin: 0 auto;
    padding: 24px;
}

.listen-actions {
    margin-bottom: 16px;
}

.webhook-feed {
    margin-top: 16px;
}
```

### Staged status (resource_row)

```css
.resource-row.staged {
    opacity: 0.6;
}

.status-dot.staged {
    background-color: var(--status-staged);
}
```

### Staged count on run cards

```css
.staged-count {
    color: var(--status-staged);
    font-size: 0.85rem;
}
```

---

## 3.11 `app.state.templates` Wiring

### In `main.py` — after `templates = Jinja2Templates(...)`:

```python
templates = Jinja2Templates(directory="templates")
# ... existing MT_DOCS and css_version setup ...

# Expose templates via app.state for webhooks.py routes
@app.on_event("startup")
async def _wire_templates():
    app.state.templates = templates
```

Or more simply, right after `app = FastAPI(...)`:

```python
app.state.templates = templates
```

The simpler approach works because `app.state.templates` is set before any
request is processed (module-level execution happens at import time, before
uvicorn starts accepting connections).

---

## File-Level Summary

| File | Changes | Lines (est.) |
|------|---------|-------------|
| `webhooks.py` | Run detail route, fire endpoint + dispatch, fire helpers, listen route, tunnel detection, `_render_webhook_row` → Jinja2, new imports | +200 |
| `main.py` | `app.state.templates`, fix `list_runs` glob filter | +5 |
| `templates/run_detail.html` | New — four-tab run detail page | +130 |
| `templates/listen.html` | New — standalone listener with tunnel banner | +50 |
| `templates/partials/webhook_row.html` | New — single webhook entry partial | +12 |
| `templates/partials/staged_row.html` | New — staged resource card with fire button | +25 |
| `templates/partials/staged_row_fired.html` | New — fired resource replacement | +15 |
| `templates/partials/resource_row.html` | Add `"staged"` status block | +5 |
| `templates/partials/run_complete.html` | Add "Details" link | +1 |
| `templates/runs.html` | Add "Details" link + staged count | +5 |
| `templates/base.html` | Add `/listen` nav tab | +1 |
| `static/style.css` | Tab strip, run detail, config viewer, detail table, staged rows, webhook rows, tunnel banner, listen page, staged status | +120 |

**Total: ~570 new lines across 12 files.**

---

## Implementation Order

| # | Task | File(s) | Lines |
|---|------|---------|-------|
| 3.1 | `app.state.templates` wiring | `main.py` | 2 |
| 3.2 | Fix `list_runs` glob filter | `main.py` | 3 |
| 3.3 | `webhook_row.html` partial | `templates/partials/` | 12 |
| 3.4 | Replace `_render_webhook_row` with Jinja2 | `webhooks.py` | 15 |
| 3.5 | `staged_row.html` + `staged_row_fired.html` partials | `templates/partials/` | 40 |
| 3.6 | "staged" status in `resource_row.html` | `templates/partials/` | 5 |
| 3.7 | Run detail route + template | `webhooks.py` + `templates/` | 165 |
| 3.8 | Fire endpoint + dispatch + helpers | `webhooks.py` | 130 |
| 3.9 | Listen route + template + tunnel detection | `webhooks.py` + `templates/` | 65 |
| 3.10 | Navigation updates (base, runs, run_complete) | `templates/` | 7 |
| 3.11 | CSS additions | `static/style.css` | 120 |

Tasks 3.1-3.6 are foundational (partials, wiring). Tasks 3.7-3.9 are the main
features. Tasks 3.10-3.11 are polish. Implement in this order to build on each
previous piece.

---

## Dependency Graph

```
3.1 (app.state.templates)
 └─> 3.4 (_render_webhook_row → Jinja2)
 └─> 3.7 (run detail route uses templates)
 └─> 3.8 (fire endpoint uses templates)
 └─> 3.9 (listen route uses templates)

3.3 (webhook_row.html)
 └─> 3.4 (_render_webhook_row references it)
 └─> 3.7 (run detail includes it)

3.5 (staged_row.html + staged_row_fired.html)
 └─> 3.7 (run detail includes staged_row)
 └─> 3.8 (fire endpoint returns staged_row_fired)

3.6 (resource_row staged status)
 └─> independent (works once engine emits "staged" SSE event, already in Step 2)

3.11 (CSS)
 └─> independent but needed for visual correctness of all new templates
```

---

## Testing Plan

### curl tests (no UI needed)

1. **Run detail page loads**: `curl -s http://localhost:8000/runs/<run_id>` → HTML with four tabs
2. **Run detail 404**: `curl -s http://localhost:8000/runs/nonexistent` → 404
3. **Fire endpoint (PO)**: Execute a config with `staged: true` on a PO, then:
   ```bash
   curl -X POST http://localhost:8000/api/runs/<run_id>/fire/payment_order.po1 \
     -d 'api_key=sk_test_...' -d 'org_id=org_...'
   ```
   → PO created in MT, manifest updated, staged JSON updated
4. **Fire endpoint (IPD)**: Same with a staged IPD → polls to completion, child refs harvested
5. **Fire endpoint (EP/LT)**: Same with staged EP and LT → created in MT
6. **Fire idempotency**: Fire the same PO twice → second call returns same ID (MT idempotency key)
7. **Listen page loads**: `curl -s http://localhost:8000/listen` → HTML with tunnel banner
8. **Tunnel detection**: Start ngrok → listen page shows public URL. Stop → shows warning.
9. **Test webhook button**: Use the JS fetch pattern → webhook appears in live feed
10. **Navigation**: Verify `/listen` tab appears in all pages, "Details" link on run cards

### Browser tests

11. **Tab switching**: Click through Config/Resources/Staged/Webhooks → SSE stays connected
12. **Fire button**: Enter creds → click Fire → row updates to "Fired" with created ID
13. **Live webhooks**: Open run detail, trigger a webhook → appears in Webhooks tab
14. **Credential persistence**: Enter creds on setup page → navigate to run detail → creds auto-filled
15. **Staged run flow (end-to-end)**: Execute config with staged POs → run complete → click Details → Staged tab → Fire → see in MT dashboard → webhook arrives → Webhooks tab shows it

---

## Risk Assessment

| Component | Complexity | Risk | Notes |
|-----------|-----------|------|-------|
| Run detail route + template | Medium | Template complexity | Four tabs, three data sources |
| Fire endpoint (PO/EP/LT) | Low | None | Simple SDK calls |
| Fire endpoint (IPD) | Medium | Polling timeout | Reuses proven tenacity pattern |
| Manifest mutation on fire | Medium | Concurrent fire safety | Single-threaded uvicorn = safe; async with locks if needed |
| Webhook row Jinja2 migration | Low | None | Drop-in replacement |
| Listen page + tunnel detect | Low | ngrok API changes | Graceful fallback to "no tunnel" |
| CSS tab system | Low | None | Standard show/hide pattern |
| `app.state.templates` | Low | None | Standard FastAPI pattern |
| list_runs glob fix | Trivial | None | |

### Concurrency note on fire endpoint

The fire endpoint mutates the manifest and staged JSON on disk. If two fire
requests arrive simultaneously (user clicks two fire buttons quickly), they
could race on the JSON read-modify-write. In practice:

1. **Single-threaded uvicorn** (default): `await` points yield control, but
   the file I/O is synchronous (`Path.read_text`, `Path.write_text`), so
   no interleaving happens between read and write.
2. **Multi-worker uvicorn**: Would need file locking. Not a concern for a
   demo tool running single-worker.

If needed, add an `asyncio.Lock` per run_id keyed in a module-level dict.

---

## Task Checklist

- [ ] 3.1 — `app.state.templates` wiring in `main.py`
- [ ] 3.2 — Fix `list_runs` glob filter in `main.py`
- [ ] 3.3 — `partials/webhook_row.html` template
- [ ] 3.4 — Replace `_render_webhook_row` in `webhooks.py` with Jinja2
- [ ] 3.5a — `partials/staged_row.html` template
- [ ] 3.5b — `partials/staged_row_fired.html` template
- [ ] 3.6 — "staged" status in `partials/resource_row.html`
- [ ] 3.7a — Run detail route in `webhooks.py`
- [ ] 3.7b — `run_detail.html` template
- [ ] 3.8a — Fire dispatch table + helpers in `webhooks.py`
- [ ] 3.8b — Fire endpoint route in `webhooks.py`
- [ ] 3.9a — Tunnel detection helper in `webhooks.py`
- [ ] 3.9b — Listen route in `webhooks.py`
- [ ] 3.9c — `listen.html` template
- [ ] 3.10a — `/listen` tab in `base.html`
- [ ] 3.10b — "Details" link + staged count in `runs.html`
- [ ] 3.10c — "Details" link in `run_complete.html`
- [ ] 3.11 — CSS additions in `static/style.css`

### Verification sequence

1. App starts without errors (`python -c "from main import app"`)
2. Run detail page renders for an existing run
3. Config tab shows JSON, Resources tab shows table, Staged tab shows staged entries
4. Fire button creates resource in MT, row updates to "Fired"
5. Webhooks tab shows historical + live webhooks
6. Listen page renders, tunnel detected/not detected
7. Test webhook button works (even with `webhook_secret` configured)
8. Navigation links work from all pages
9. CSS renders correctly in browser
10. No regressions on existing pages (setup, preview, execute, runs, cleanup)

---

## Review Fixes (from `plan/reviews/mt_dataloader_step3_ui_review.md`)

| # | Issue | Severity | Resolution |
|---|-------|----------|------------|
| 1 | Test webhook button fails silently with `webhook_secret` | Visible breakage | **Fixed**: server-side `POST /api/webhooks/test` endpoint that injects a synthetic `WebhookEntry` directly into ring buffer + fanout + JSONL, bypassing signature verification. Button posts to `/api/webhooks/test` instead of `/webhooks/mt`. |
| 2 | IPD fire poll doesn't exit on terminal failure states | 30s hang | **Fixed**: added `_IPD_TERMINAL = {"completed", "returned", "failed"}` set; retry predicate checks `r.status not in _IPD_TERMINAL`; post-poll check raises `HTTPException(502)` if status != "completed". |
| 3 | Concurrent fire race condition wider than described | Data corruption (rare) | **Fixed**: added `_fire_locks: dict[str, asyncio.Lock]` with per-run locking around the entire read-fire-write block in `fire_staged()`. The spec's claim about sync I/O preventing races was incorrect — the `await _fire_one()` yields to the event loop between file reads and writes. |
| 4 | Dead import `_poll_ipd_status` | Cleanup | **Fixed**: removed from import; fire endpoint re-implements polling (as designed). |
| 5 | Two separate Jinja2 environments | Maintenance risk | **Fixed**: pre-render webhook row HTML in the receiver route (where `request.app.state.templates` is available), store the rendered HTML on a new `WebhookEntry.html` field, and yield it directly in the SSE generator. Eliminates the second environment entirely. |
| 6 | Negative glob filter is fragile | Maintenance risk | **Fixed**: positive regex match `r"^\d{8}T\d{6}_[0-9a-f]{8}\.json$"` on manifest filenames (matches `generate_run_id()` format exactly). |
| 7 | JSONL not matched by `*.json` glob | Non-issue | Confirmed: `.jsonl` files don't match `*.json`. |

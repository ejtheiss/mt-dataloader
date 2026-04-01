"""Ngrok tunnel lifecycle management.

Provides :class:`TunnelManager` which wraps *pyngrok* to start/stop an
ngrok tunnel and persists config (authtoken, domain, webhook endpoint ID)
to ``runs/.tunnel_config.json`` so that settings survive Docker container
restarts via the volume mount.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from jsonutil import dumps_pretty, loads_path
from loguru import logger

_CONFIG_FILENAME = ".tunnel_config.json"

_AGENTS_DASHBOARD = "https://dashboard.ngrok.com/agents"


class NgrokStartError(Exception):
    """Raised when pyngrok cannot open a tunnel (includes account / quota hints)."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def interpret_ngrok_start_error(exc: BaseException) -> dict[str, str | None]:
    """Map raw pyngrok / ngrok agent errors to optional ``ERR_*`` code and hint text."""
    raw = str(exc).strip()
    lower = raw.lower()
    code: str | None = None
    if "ERR_NGROK_108" in raw or "err_ngrok_108" in lower:
        code = "ERR_NGROK_108"
    elif "3 simultaneous" in lower and "ngrok" in lower:
        code = "ERR_NGROK_108"
    elif "limited to 3" in lower and "session" in lower:
        code = "ERR_NGROK_108"

    hint: str | None = None
    if code == "ERR_NGROK_108":
        hint = (
            "Free ngrok allows only 3 concurrent agent sessions for your account. "
            "Each separate `ngrok` process (other terminals, Docker containers, IDE tunnels, "
            "or old dataloader instances) counts as one session. "
            f"Open {_AGENTS_DASHBOARD} to disconnect idle agents, stop extra processes, "
            "or set DATALOADER_NGROK_AUTO_START=false if you already run ngrok yourself."
        )
    return {"raw": raw, "code": code, "hint": hint}


class TunnelManager:
    """Manages ngrok tunnel lifecycle and persistent config.

    Settings priority: env-var override > persisted config > empty.
    """

    def __init__(self, runs_dir: str = "runs") -> None:
        self._config_path = Path(runs_dir) / _CONFIG_FILENAME
        self._active_url: str | None = None
        self._config: dict = self._load_config()
        self._ngrok_failure: dict | None = None

    def clear_ngrok_failure(self) -> None:
        """Clear last start failure (called after a successful connect)."""
        self._ngrok_failure = None

    def last_ngrok_failure(self) -> dict | None:
        """Last tunnel start failure for UI: ``code``, ``message``, ``hint``, ``at``."""
        return self._ngrok_failure

    def _set_ngrok_failure(
        self,
        code: str | None,
        message: str,
        hint: str | None,
    ) -> None:
        self._ngrok_failure = {
            "code": code,
            "message": message,
            "hint": hint,
            "at": datetime.now(timezone.utc).isoformat(),
        }

    def record_unknown_start_failure(self, message: str) -> None:
        """Persist a tunnel start error that was not a structured ``NgrokStartError``."""
        self._set_ngrok_failure(None, message, None)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_config(self) -> dict:
        if self._config_path.exists():
            try:
                data = loads_path(self._config_path)
                return data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                logger.warning("Could not load tunnel config: {}", exc)
        return {}

    def _save_config(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(dumps_pretty(self._config), encoding="utf-8")

    # ------------------------------------------------------------------
    # Tunnel lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        authtoken: str,
        port: int = 8000,
        domain: str | None = None,
    ) -> str:
        """Start an ngrok tunnel.  Returns the public HTTPS URL."""
        from pyngrok import conf, ngrok

        conf.get_default().auth_token = authtoken
        try:
            ngrok.disconnect_all()
        except Exception:
            pass

        kwargs: dict = {"addr": str(port), "proto": "http"}
        if domain:
            kwargs["hostname"] = domain

        try:
            tunnel = ngrok.connect(**kwargs)
        except Exception as exc:
            info = interpret_ngrok_start_error(exc)
            msg = info["hint"] or info["raw"]
            self._set_ngrok_failure(info["code"], msg, info["hint"])
            raise NgrokStartError(
                msg,
                code=info["code"],
                hint=info["hint"],
            ) from exc

        self.clear_ngrok_failure()
        self._active_url = tunnel.public_url
        logger.info("Tunnel started: {}", self._active_url)

        self._config["authtoken"] = authtoken
        if domain:
            self._config["domain"] = domain
        self._save_config()

        return self._active_url

    def stop(self) -> None:
        """Disconnect all tunnels."""
        try:
            from pyngrok import ngrok

            ngrok.disconnect_all()
            ngrok.kill()
        except Exception as exc:
            logger.debug("Tunnel stop: {}", exc)
        self._active_url = None
        logger.info("Tunnel stopped")

    def get_status(self) -> dict:
        """Return ``{connected, url}`` by checking pyngrok then the local agent API."""
        try:
            from pyngrok import ngrok

            tunnels = ngrok.get_tunnels()
            for t in tunnels:
                if t.public_url.startswith("https://"):
                    out = {"connected": True, "url": t.public_url}
                    self.clear_ngrok_failure()
                    return out
        except Exception:
            pass

        ext = _probe_external_ngrok()
        if ext.get("connected"):
            self.clear_ngrok_failure()
        return ext

    # ------------------------------------------------------------------
    # Saved properties
    # ------------------------------------------------------------------

    @property
    def saved_authtoken(self) -> str:
        return self._config.get("authtoken", "")

    @property
    def saved_domain(self) -> str:
        return self._config.get("domain", "")

    @property
    def saved_webhook_endpoint_id(self) -> str:
        return self._config.get("webhook_endpoint_id", "")

    @property
    def saved_webhook_key(self) -> str:
        return self._config.get("webhook_key", "")

    def save_webhook_endpoint(self, endpoint_id: str, webhook_key: str) -> None:
        self._config["webhook_endpoint_id"] = endpoint_id
        self._config["webhook_key"] = webhook_key
        self._save_config()


def first_https_tunnel_url(tunnels_payload: dict) -> str | None:
    """Return the first ``https`` ``public_url`` from ngrok agent ``/api/tunnels`` JSON."""
    for tunnel in tunnels_payload.get("tunnels", []):
        url = tunnel.get("public_url", "")
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return None


def _probe_external_ngrok() -> dict:
    """Probe the ngrok local agent API (for externally-run ngrok)."""
    try:
        with httpx.Client(timeout=2.0) as http:
            resp = http.get("http://127.0.0.1:4040/api/tunnels")
            if resp.status_code == 200:
                data = resp.json()
                url = first_https_tunnel_url(data)
                if url:
                    return {"connected": True, "url": url}
    except Exception:
        pass
    return {"connected": False, "url": None}

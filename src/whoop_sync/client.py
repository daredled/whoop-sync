"""OAuth 2.0 and HTTP client for the WHOOP Developer API v2 (standard library only)."""

from __future__ import annotations

import http.server
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_URL = "https://api.prod.whoop.com/developer"
DEFAULT_SCOPES = (
    "offline read:recovery read:sleep read:workout read:cycles read:profile read:body_measurement"
)
USER_AGENT = "whoop-sync/0.1"
PAGE_LIMIT = 25  # maximum allowed by the API


class WhoopError(RuntimeError):
    """Any failure talking to WHOOP (HTTP error, bad OAuth state, missing tokens)."""


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str
    redirect_uri: str = "http://127.0.0.1:3000/callback"

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Credentials:
        """Read WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET / WHOOP_REDIRECT_URI.

        Real environment variables win over values in `env_file`.
        """
        values = _read_env_file(env_file) if env_file and env_file.exists() else {}
        values.update({k: v for k, v in os.environ.items() if k.startswith("WHOOP_")})
        missing = [k for k in ("WHOOP_CLIENT_ID", "WHOOP_CLIENT_SECRET") if not values.get(k)]
        if missing:
            raise WhoopError(f"Missing {', '.join(missing)} (environment or {env_file})")
        return cls(
            values["WHOOP_CLIENT_ID"],
            values["WHOOP_CLIENT_SECRET"],
            values.get("WHOOP_REDIRECT_URI") or cls.redirect_uri,
        )


def _read_env_file(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def default_tokens_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "whoop-sync" / "tokens.json"


def _request_json(url: str, data: dict | None = None, headers: dict | None = None,
                  retries: int = 3) -> dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, **(headers or {})})
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or e.code >= 500
            if retryable and attempt < retries:
                time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                continue
            detail = e.read().decode(errors="replace")[:500]
            raise WhoopError(f"HTTP {e.code} from {url.split('?')[0]}: {detail}") from e
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise WhoopError(f"Cannot reach {url.split('?')[0]}: {e.reason}") from e
    raise AssertionError("unreachable")


class WhoopClient:
    def __init__(self, credentials: Credentials, tokens_file: Path | None = None,
                 scopes: str = DEFAULT_SCOPES):
        self.creds = credentials
        self.tokens_file = tokens_file or default_tokens_file()
        self.scopes = scopes

    # ---- OAuth -------------------------------------------------------------

    def authorization_url(self, state: str) -> str:
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": self.creds.client_id,
            "redirect_uri": self.creds.redirect_uri,
            "scope": self.scopes,
            "state": state,
        })

    @staticmethod
    def new_state() -> str:
        return secrets.token_urlsafe(12)  # WHOOP requires at least 8 characters

    def wait_for_redirect(self, state: str) -> str:
        """Serve the redirect URI locally until WHOOP calls back; return the auth code."""
        redirect = urllib.parse.urlparse(self.creds.redirect_uri)
        result: dict[str, str] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                result.update({k: v[0] for k, v in q.items()})
                ok = "code" in result and result.get("state") == state
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                msg = "Authorized. You can close this tab." if ok else "Authorization failed."
                self.wfile.write(f"<h2>{msg}</h2>".encode())

            def log_message(self, *args):
                pass

        with http.server.HTTPServer((redirect.hostname, redirect.port or 80), Handler) as srv:
            while "code" not in result and "error" not in result:
                srv.handle_request()
        return self.code_from_redirect_params(result, state)

    @staticmethod
    def code_from_redirect_url(url: str, state: str) -> str:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        return WhoopClient.code_from_redirect_params({k: v[0] for k, v in q.items()}, state)

    @staticmethod
    def code_from_redirect_params(params: dict[str, str], state: str) -> str:
        if "error" in params:
            raise WhoopError(f"WHOOP returned an error: {params}")
        if params.get("state") != state:
            raise WhoopError("OAuth state mismatch; start the authorization again")
        if "code" not in params:
            raise WhoopError("Redirect has no authorization code")
        return params["code"]

    def exchange_code(self, code: str) -> dict:
        tokens = _request_json(TOKEN_URL, {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.creds.client_id,
            "client_secret": self.creds.client_secret,
            "redirect_uri": self.creds.redirect_uri,
        })
        self._save_tokens(tokens)
        return tokens

    def _save_tokens(self, tokens: dict) -> None:
        tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 3600)) - 60
        self.tokens_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.tokens_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(tokens, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.tokens_file)

    def access_token(self) -> str:
        if not self.tokens_file.exists():
            raise WhoopError(f"No tokens at {self.tokens_file}; run `whoop-sync auth` first")
        tokens = json.loads(self.tokens_file.read_text())
        if time.time() < tokens.get("expires_at", 0):
            return tokens["access_token"]
        if "refresh_token" not in tokens:
            raise WhoopError("Access token expired and no refresh token (scope 'offline' missing?)")
        # WHOOP rotates refresh tokens: the old one is invalid as soon as this call succeeds,
        # so the new pair is written to disk before anything else can fail.
        new = _request_json(TOKEN_URL, {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": self.creds.client_id,
            "client_secret": self.creds.client_secret,
            "scope": "offline",
        })
        self._save_tokens(new)
        return new["access_token"]

    # ---- API ---------------------------------------------------------------

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{API_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return _request_json(url, headers={"Authorization": f"Bearer {self.access_token()}"})

    def collection(self, path: str, start: str, end: str) -> list[dict]:
        """Fetch every page of a v2 collection between ISO-8601 `start` and `end`."""
        records, next_token = [], None
        while True:
            params = {"limit": PAGE_LIMIT, "start": start, "end": end}
            if next_token:
                params["nextToken"] = next_token
            page = self.get(path, params)
            records += page.get("records", [])
            next_token = page.get("next_token")
            if not next_token:
                return records

    def fetch_all(self, start: str, end: str) -> dict[str, list[dict]]:
        return {
            "cycles": self.collection("/v2/cycle", start, end),
            "recoveries": self.collection("/v2/recovery", start, end),
            "sleeps": self.collection("/v2/activity/sleep", start, end),
            "workouts": self.collection("/v2/activity/workout", start, end),
        }

    def body_measurement(self) -> dict:
        return self.get("/v2/user/measurement/body")

    def profile(self) -> dict:
        return self.get("/v2/user/profile/basic")

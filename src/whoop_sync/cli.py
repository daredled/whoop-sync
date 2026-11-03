"""Command line interface: `whoop-sync auth` and `whoop-sync sync`."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .client import Credentials, WhoopClient, WhoopError, default_tokens_file
from .summary import COLUMNS, summarize_days


def _client(args) -> WhoopClient:
    return WhoopClient(Credentials.from_env(args.env_file), args.tokens_file)


def cmd_auth(args) -> None:
    client = _client(args)
    state = client.new_state()
    print("Open this URL in a browser and approve access:\n\n" + client.authorization_url(state) + "\n")
    if args.manual:
        url = input("Paste the full URL you were redirected to (even if the page shows an error): ")
        code = client.code_from_redirect_url(url.strip(), state)
    else:
        print(f"Waiting for the redirect to {client.creds.redirect_uri} ... "
              "(Ctrl+C to cancel; if it never arrives, use `auth --manual`)")
        code = client.wait_for_redirect(state)
    tokens = client.exchange_code(code)
    if "refresh_token" not in tokens:
        print("Warning: no refresh token received. Add the 'offline' scope to your WHOOP app.")
    print(f"Authorized. Tokens saved to {client.tokens_file}")


def merge_csv(path: Path, rows: dict[str, dict]) -> None:
    """Upsert rows by date, field by field.

    New values replace old ones, but an empty new value never erases an existing one:
    the oldest day of a sync window often comes back partial (e.g. its recovery belongs
    to a cycle that started before the window).
    """
    existing: dict[str, dict] = {}
    if path.exists():
        with path.open(newline="") as f:
            existing = {r["date"]: r for r in csv.DictReader(f)}
    for d, row in rows.items():
        merged = dict(existing.get(d, {}))
        merged.update({k: v for k, v in row.items() if v not in ("", None)})
        merged["date"] = d
        existing[d] = merged
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for d in sorted(existing):
            writer.writerow(existing[d])


def cmd_sync(args) -> None:
    client = _client(args)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    data = client.fetch_all(start.strftime(fmt), end.strftime(fmt))

    if args.raw_dir:
        args.raw_dir.mkdir(parents=True, exist_ok=True)
        data_with_body = {**data, "body": client.body_measurement()}
        for name, payload in data_with_body.items():
            (args.raw_dir / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    rows = summarize_days(data)
    merge_csv(args.csv, rows)
    n = sum(len(v) for v in data.values())
    print(f"{len(rows)} days updated in {args.csv} ({n} records).")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="whoop-sync", description=__doc__)
    p.add_argument("--env-file", type=Path, default=Path(".env"),
                   help="file with WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET / WHOOP_REDIRECT_URI (default: ./.env)")
    p.add_argument("--tokens-file", type=Path, default=default_tokens_file(),
                   help="where OAuth tokens are stored (default: %(default)s)")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth", help="authorize access to your WHOOP account (run once)")
    a.add_argument("--manual", action="store_true",
                   help="paste the redirect URL instead of running a local callback server")
    a.set_defaults(func=cmd_auth)

    s = sub.add_parser("sync", help="download recent data and update the daily CSV")
    s.add_argument("--days", type=int, default=14, help="how many days back to download (default: 14)")
    s.add_argument("--csv", type=Path, default=Path("whoop_daily.csv"), help="daily CSV to update (default: %(default)s)")
    s.add_argument("--raw-dir", type=Path, help="also save the raw API responses as JSON in this directory")
    s.set_defaults(func=cmd_sync)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except WhoopError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

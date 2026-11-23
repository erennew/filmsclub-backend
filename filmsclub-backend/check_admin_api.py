"""Check that a *running* backend serves every route the React admin console needs.

The console's "Not Found" errors are not a frontend bug: the connected backend
is an older build that predates those routes. This script logs in, reads the
backend's own route table and probes each required GET endpoint, so you can
prove which deploy is stale before restarting/redeploying.

Usage (from anywhere)::

    python check_admin_api.py --url https://your-backend.example.com \
        --user admin --password 'your-password'

    # or reuse an existing admin session token
    python check_admin_api.py --url http://localhost:8000 --token <jwt>

Credentials may also come from ADMIN_WEB_USERNAME / ADMIN_WEB_PASSWORD.
Only stdlib is used; no project imports, so it works outside the venv too.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Keep in sync with REQUIRED_ADMIN_ROUTES in frontend-main/src/admin/api.js.
REQUIRED_GET_ROUTES = [
    "/admin/api/capabilities",
    "/admin/api/files/lookup",
    "/admin/api/files/reassign",
    "/admin/api/session",
    "/admin/api/media",
    "/admin/api/hero",
    "/admin/api/index-scans",
    "/admin/api/orphans",
    "/admin/api/audio-curation",
    "/admin/api/config",
    "/admin/api/audit",
    "/admin/api/queue",
    "/admin/api/jobs",
    "/admin/api/health",
    "/admin/api/cache",
    "/admin/api/channels",
    "/admin/api/failed-files",
    "/admin/api/library-health",
    "/admin/api/analytics",
    "/admin/api/views-analytics",
    "/admin/api/storage",
]

EXPECTED_BUILD = "2026-11-07"
TIMEOUT = 15


def call(url: str, method: str = "GET", body=None, token: str = ""):
    """Return (status, parsed_json_or_text). Network errors become status 0."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Admin-Session"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            raw = res.read().decode("utf-8", "replace")
            status = res.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    except Exception as e:  # noqa: BLE001 - report any transport failure
        return 0, str(e)
    try:
        return status, json.loads(raw)
    except (ValueError, TypeError):
        return status, raw[:300]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("ADMIN_API_URL", "http://localhost:8000"),
                        help="Backend origin, e.g. https://your-app.herokuapp.com")
    parser.add_argument("--user", default=os.getenv("ADMIN_WEB_USERNAME", "admin"))
    parser.add_argument("--password", default=os.getenv("ADMIN_WEB_PASSWORD", ""))
    parser.add_argument("--token", default=os.getenv("ADMIN_SESSION_TOKEN", ""),
                        help="Existing X-Admin-Session token (skips login)")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    token = args.token
    if not token:
        if not args.password:
            print("Provide --password, --token, or set ADMIN_WEB_PASSWORD.")
            return 2
        status, body = call(base + "/admin/api/login-token", "POST",
                            {"username": args.user, "password": args.password})
        if status != 200 or not isinstance(body, dict) or not body.get("token"):
            print(f"Login failed (HTTP {status}): {body}")
            return 2
        token = body["token"]
        print(f"Login OK as {args.user}")

    print(f"\nBackend: {base}")
    status, info = call(base + "/admin/api/capabilities", token=token)
    if status == 404 or not isinstance(info, dict):
        print(f"  capabilities: HTTP {status} — this backend predates the compatibility endpoint.")
        print("  => It is an OLD build. Deploy/restart the current backend, then rerun this check.")
    else:
        print(f"  version {info.get('version')} · build {info.get('build')} "
              f"· uptime {info.get('uptime_seconds')}s")
        if info.get("build") != EXPECTED_BUILD:
            print(f"  WARNING: console expects build {EXPECTED_BUILD}.")

    print("\nRequired GET routes:")
    missing = []
    for path in REQUIRED_GET_ROUTES:
        code, _payload = call(base + path, token=token)
        flag = "OK" if code not in (0, 404) else "MISSING" if code == 404 else "UNREACHABLE"
        if flag != "OK":
            missing.append(path)
        print(f"  {flag:<11} {code:<4} {path}")

    print()
    if missing:
        print(f"{len(missing)} route(s) missing:")
        for path in missing:
            print(f"  - {path}")
        print("\nFIX: deploy the current filmsclub-backend/Backend code (or restart a stale")
        print("local process), then rebuild/reload the admin console. A frontend-only")
        print("redeploy cannot add backend routes.")
        return 1
    print("All admin routes are present. If a page still shows 'Not Found', check the")
    print("console's backend URL in connection settings — it may point at another host.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

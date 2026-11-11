#!/usr/bin/env python3
"""Probe a live ERPNext instance and print discovery facts.

Stage 1 goal: prove ERPNext is reachable and record real API behavior.
Does not invent DocType schemas — it only reports what the server returns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[1]
DOCTYPES = [
    "Company",
    "Supplier",
    "Item",
    "Warehouse",
    "Purchase Order",
    "Purchase Receipt",
    "Purchase Invoice",
]


class ProbeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    erpnext_base_url: str = "http://localhost:8080"
    erpnext_site: str = "frontend"
    erpnext_admin_user: str = "Administrator"
    erpnext_admin_password: str = "admin"
    erpnext_api_key: str | None = None
    erpnext_api_secret: str | None = None
    erpnext_timeout_seconds: float = 30.0


def _headers(settings: ProbeSettings, sid: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Host": settings.erpnext_site,
        "X-Frappe-Site-Name": settings.erpnext_site,
    }
    if settings.erpnext_api_key and settings.erpnext_api_secret:
        headers["Authorization"] = (
            f"token {settings.erpnext_api_key}:{settings.erpnext_api_secret}"
        )
    elif sid:
        headers["Cookie"] = f"sid={sid}"
    return headers


def login(client: httpx.Client, settings: ProbeSettings) -> str:
    response = client.post(
        "/api/method/login",
        data={
            "usr": settings.erpnext_admin_user,
            "pwd": settings.erpnext_admin_password,
        },
        headers={"Host": settings.erpnext_site, "X-Frappe-Site-Name": settings.erpnext_site},
    )
    response.raise_for_status()
    sid = response.cookies.get("sid")
    if not sid:
        raise RuntimeError("Login succeeded but no sid cookie was returned.")
    return sid


def list_doctype(
    client: httpx.Client,
    settings: ProbeSettings,
    doctype: str,
    sid: str | None,
) -> dict[str, Any]:
    response = client.get(
        f"/api/resource/{doctype}",
        params={"limit_page_length": 5},
        headers=_headers(settings, sid),
    )
    return {
        "doctype": doctype,
        "status_code": response.status_code,
        "ok": response.is_success,
        "body_preview": _safe_preview(response),
    }


def _safe_preview(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return response.text[:400]
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        if isinstance(data, list):
            return {"count_returned": len(data), "sample": data[:2]}
        return data
    return payload


def ping(client: httpx.Client, settings: ProbeSettings) -> dict[str, Any]:
    response = client.get(
        "/api/method/frappe.ping",
        headers={"Host": settings.erpnext_site, "X-Frappe-Site-Name": settings.erpnext_site},
    )
    return {
        "url": str(response.request.url),
        "status_code": response.status_code,
        "body": _safe_preview(response),
    }


def main() -> int:
    settings = ProbeSettings()
    print(f"Probing ERPNext at {settings.erpnext_base_url} (site={settings.erpnext_site})")

    try:
        with httpx.Client(
            base_url=settings.erpnext_base_url.rstrip("/"),
            timeout=settings.erpnext_timeout_seconds,
            follow_redirects=True,
        ) as client:
            ping_result = ping(client, settings)
            print("\n== ping ==")
            print(json.dumps(ping_result, indent=2))

            sid: str | None = None
            if not (settings.erpnext_api_key and settings.erpnext_api_secret):
                print("\n== login ==")
                sid = login(client, settings)
                print(f"Authenticated via session cookie (sid present: {bool(sid)})")
            else:
                print("\n== auth ==")
                print("Using ERPNEXT_API_KEY / ERPNEXT_API_SECRET token auth")

            print("\n== DocType list probes ==")
            results = [list_doctype(client, settings, doctype, sid) for doctype in DOCTYPES]
            print(json.dumps(results, indent=2))

            failed = [r for r in results if not r["ok"]]
            if failed:
                print(
                    "\nSome DocType probes failed. "
                    "ERPNext may still be creating the site, or auth/site headers are wrong.",
                    file=sys.stderr,
                )
                return 1

            print(
                "\nProbe succeeded. Update docs/erpnext-discovery.md with these verified results."
            )
            return 0
    except httpx.ConnectError as exc:
        print(
            f"\nCould not connect to ERPNext: {exc}\n"
            "Run: make up-erpnext && make wait-erpnext",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"\nProbe failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

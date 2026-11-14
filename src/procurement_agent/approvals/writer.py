"""Create a draft purchase order in ERPNext. Only the approval vault may call this."""

from __future__ import annotations

import json
from urllib.parse import quote

import httpx

from procurement_agent.approvals.orders import DraftPurchaseOrder
from procurement_agent.config import Settings


class ErpNextDraftWriter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.erpnext_base_url.rstrip("/"),
            timeout=settings.erpnext_timeout_seconds,
            follow_redirects=True,
        )
        self._sid: str | None = None

    def close(self) -> None:
        self._client.close()

    def create_draft(self, order: DraftPurchaseOrder) -> str:
        self._login()
        response = self._client.post(
            "/api/resource/Purchase Order",
            headers=self._headers(),
            json={
                "doctype": "Purchase Order",
                "supplier": order.supplier,
                "company": order.company,
                "transaction_date": order.transaction_date.isoformat(),
                "schedule_date": order.schedule_date.isoformat(),
                "currency": order.currency,
                "conversion_rate": 1,
                "items": [
                    {
                        "item_code": line.item_code,
                        "qty": line.qty,
                        "rate": line.rate,
                        "schedule_date": order.schedule_date.isoformat(),
                        "warehouse": order.warehouse,
                    }
                    for line in order.items
                ],
            },
        )
        response.raise_for_status()
        name = response.json().get("data", {}).get("name")
        if not isinstance(name, str) or not name:
            raise RuntimeError("ERPNext did not return a purchase order name.")
        return name

    def submit(self, name: str) -> None:
        """Submit the latest saved document. A name-only payload is rejected as stale."""
        self._login()
        current = self._client.get(
            f"/api/resource/Purchase Order/{quote(name, safe='')}",
            headers=self._headers(),
        )
        current.raise_for_status()
        document = current.json().get("data")
        if not isinstance(document, dict):
            raise RuntimeError("ERPNext did not return the purchase order.")
        response = self._client.post(
            "/api/method/frappe.client.submit",
            headers=self._headers(),
            data={"doc": json.dumps(document, default=str)},
        )
        if response.is_error:
            raise RuntimeError(_erp_error(response))

    def _login(self) -> None:
        if self._sid or (self._settings.erpnext_api_key and self._settings.erpnext_api_secret):
            return
        response = self._client.post(
            "/api/method/login",
            headers=self._headers(),
            data={
                "usr": self._settings.erpnext_admin_user,
                "pwd": self._settings.erpnext_admin_password.get_secret_value(),
            },
        )
        response.raise_for_status()
        self._sid = response.cookies.get("sid")
        if not self._sid:
            raise RuntimeError("ERPNext login did not return a session.")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Host": self._settings.erpnext_site,
            "X-Frappe-Site-Name": self._settings.erpnext_site,
        }
        if self._settings.erpnext_api_key and self._settings.erpnext_api_secret:
            key = self._settings.erpnext_api_key.get_secret_value()
            secret = self._settings.erpnext_api_secret.get_secret_value()
            headers["Authorization"] = f"token {key}:{secret}"
        elif self._sid:
            headers["Cookie"] = f"sid={self._sid}"
        return headers


def _erp_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"ERPNext refused the submit ({response.status_code})."
    exception = str(body.get("exception") or "")
    detail = exception.split(":", 1)[-1].strip().split("\n", 1)[0]
    return detail[:240] or f"ERPNext refused the submit ({response.status_code})."

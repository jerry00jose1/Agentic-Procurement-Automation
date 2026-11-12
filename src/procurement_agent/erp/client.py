"""Read-only ERPNext client.

Login is the only POST. Document lists and counts use GET.
Child tables return fields only when `parent` is the parent DocType.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from procurement_agent.config import Settings

PAGE_SIZE = 200


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class ErpNextReadClient:
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

    def __enter__(self) -> ErpNextReadClient:
        self.login()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def login(self) -> None:
        if self._settings.erpnext_api_key and self._settings.erpnext_api_secret:
            return
        response = self._request(
            "POST",
            "/api/method/login",
            data={
                "usr": self._settings.erpnext_admin_user,
                "pwd": self._settings.erpnext_admin_password.get_secret_value(),
            },
        )
        self._sid = response.cookies.get("sid")
        if not self._sid:
            raise RuntimeError("ERPNext login did not return a session.")

    def list_all(
        self,
        doctype: str,
        fields: list[str],
        *,
        filters: list[list[Any]] | None = None,
        parent: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            params: dict[str, Any] = {
                "doctype": doctype,
                "fields": json.dumps(fields),
                "limit_start": start,
                "limit_page_length": PAGE_SIZE,
            }
            if filters:
                params["filters"] = json.dumps(filters)
            if parent:
                params["parent"] = parent
            payload = self._request(
                "GET",
                "/api/method/frappe.client.get_list",
                params=params,
            ).json()
            page = payload.get("message") or []
            if page and fields != ["name"] and set(page[0]) == {"name"}:
                raise RuntimeError(
                    f"ERPNext returned only names for {doctype}. Pass parent={parent!r}."
                )
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            start += PAGE_SIZE

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

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        reraise=True,
    )
    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, url, headers=self._headers(), **kwargs)
        response.raise_for_status()
        return response

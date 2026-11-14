import json
from datetime import date
from urllib.parse import parse_qs

import httpx
import respx

from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.approvals.writer import ErpNextDraftWriter
from procurement_agent.config import Settings


def test_writer_posts_a_draft_and_does_not_submit() -> None:
    order = DraftPurchaseOrder(
        supplier="Delta Distribution LLC 068",
        company="Northwind Components",
        warehouse="Stores - NC",
        transaction_date=date(2024, 6, 1),
        schedule_date=date(2024, 6, 15),
        items=[PurchaseOrderLine(item_code="M01-01", qty=10, rate=4.5)],
    )
    settings = Settings(
        erpnext_base_url="http://erpnext.test",
        erpnext_site="frontend",
        erpnext_admin_user="Administrator",
        erpnext_admin_password="admin",
    )
    with respx.mock(base_url="http://erpnext.test") as router:
        router.post("/api/method/login").mock(
            return_value=httpx.Response(200, headers={"set-cookie": "sid=test-sid; Path=/"})
        )
        create = router.post("/api/resource/Purchase Order").mock(
            return_value=httpx.Response(200, json={"data": {"name": "PUR-ORD-2026-00999"}})
        )
        writer = ErpNextDraftWriter(settings)
        try:
            name = writer.create_draft(order)
        finally:
            writer.close()

    assert name == "PUR-ORD-2026-00999"
    body = json.loads(create.calls.last.request.content)
    assert "docstatus" not in body
    assert body["supplier"] == "Delta Distribution LLC 068"
    assert body["items"][0]["qty"] == 10
    assert body["items"][0]["rate"] == 4.5
    assert body["items"][0]["warehouse"] == "Stores - NC"


def test_submit_sends_the_latest_document() -> None:
    settings = Settings(
        erpnext_base_url="http://erpnext.test",
        erpnext_site="frontend",
        erpnext_admin_user="Administrator",
        erpnext_admin_password="admin",
    )
    with respx.mock(base_url="http://erpnext.test") as router:
        router.post("/api/method/login").mock(
            return_value=httpx.Response(200, headers={"set-cookie": "sid=test-sid; Path=/"})
        )
        router.get("/api/resource/Purchase Order/PUR-ORD-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "doctype": "Purchase Order",
                        "name": "PUR-ORD-1",
                        "modified": "2026-11-14 12:06:36.040874",
                    }
                },
            )
        )
        submit = router.post("/api/method/frappe.client.submit").mock(
            return_value=httpx.Response(200, json={"message": "ok"})
        )
        writer = ErpNextDraftWriter(settings)
        try:
            writer.submit("PUR-ORD-1")
        finally:
            writer.close()
    sent = json.loads(parse_qs(submit.calls.last.request.content.decode())["doc"][0])
    assert sent["modified"] == "2026-11-14 12:06:36.040874"
    assert sent["name"] == "PUR-ORD-1"
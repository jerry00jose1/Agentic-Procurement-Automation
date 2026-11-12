import json
from datetime import date

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
from unittest.mock import patch
from fastapi.testclient import TestClient
from models import UpsellSuggestion
import pytest


@pytest.fixture
def client(isolated_audit_db, monkeypatch):
    import main
    import importlib
    importlib.reload(main)
    # Force auth off for this fixture regardless of what's in .env — reload()
    # re-runs load_dotenv(), which would otherwise silently repopulate
    # MERCHANT_API_KEY from disk and break every "no auth" test.
    main.MERCHANT_API_KEY = None
    return TestClient(main.app)


@pytest.fixture
def authed_client(isolated_audit_db, monkeypatch):
    import main
    import importlib
    importlib.reload(main)
    main.MERCHANT_API_KEY = "test-key-123"
    return TestClient(main.app), main


def _cart_body(session_id):
    return {"session_id": session_id, "items": [{"sku": "sku_004", "name": "Mechanical Keyboard", "qty": 1, "price_paise": 349900}]}


def test_catalog_and_cart_scenarios_are_public(client):
    assert client.get("/catalog").status_code == 200
    assert client.get("/cart-scenarios").status_code == 200


def test_full_auto_approve_flow(client):
    import main
    with patch.object(main.agent_module, "decide_upsell") as mock_decide, \
         patch.object(main.razorpay_client, "create_payment_link") as mock_rzp:
        mock_decide.return_value = UpsellSuggestion(should_upsell=True, sku="sku_009", discount_pct=5, reasoning="cheap")
        mock_rzp.return_value = {"ok": True, "payment_link": {"short_url": "https://rzp.io/i/x"}}
        r = client.post("/decide", json=_cart_body("t_auto"))
        assert r.status_code == 200
        assert r.json()["status"] == "auto_approved"
        assert r.json()["payment_link"] == "https://rzp.io/i/x"


def test_full_human_approval_flow_including_double_approve_rejected(client):
    import main
    with patch.object(main.agent_module, "decide_upsell") as mock_decide:
        mock_decide.return_value = UpsellSuggestion(should_upsell=True, sku="sku_005", discount_pct=10, reasoning="mid value")
        r = client.post("/decide", json=_cart_body("t_pending"))
        assert r.json()["status"] == "pending_human_approval"
        decision_id = r.json()["decision_id"]

    with patch.object(main.razorpay_client, "create_payment_link") as mock_rzp:
        mock_rzp.return_value = {"ok": True, "payment_link": {"short_url": "https://rzp.io/i/approved"}}
        r = client.post(f"/decide/{decision_id}/approve")
        assert r.json()["status"] == "approved_and_created"

    r = client.post(f"/decide/{decision_id}/approve")
    assert r.status_code == 404


def test_blocked_flow(client):
    import main
    with patch.object(main.agent_module, "decide_upsell") as mock_decide:
        mock_decide.return_value = UpsellSuggestion(should_upsell=True, sku="sku_nonexistent", discount_pct=5, reasoning="hallucinated")
        r = client.post("/decide", json=_cart_body("t_blocked"))
        assert r.json()["status"] == "blocked"


def test_razorpay_failure_recovers_gracefully_not_500(client):
    import main
    with patch.object(main.agent_module, "decide_upsell") as mock_decide, \
         patch.object(main.razorpay_client, "create_payment_link") as mock_rzp:
        mock_decide.return_value = UpsellSuggestion(should_upsell=True, sku="sku_009", discount_pct=0, reasoning="x")
        mock_rzp.return_value = {"ok": False, "error_type": "bad_request", "error_message": "boom"}
        r = client.post("/decide", json=_cart_body("t_fail"))
        assert r.status_code == 200
        assert r.json()["status"] == "failed_gracefully"


def test_idempotency_key_prevents_duplicate_side_effects(client):
    import main
    headers = {"Idempotency-Key": "retry-key-1"}
    with patch.object(main.agent_module, "decide_upsell") as mock_decide, \
         patch.object(main.razorpay_client, "create_payment_link") as mock_rzp:
        mock_decide.return_value = UpsellSuggestion(should_upsell=True, sku="sku_009", discount_pct=0, reasoning="x")
        mock_rzp.return_value = {"ok": True, "payment_link": {"short_url": "https://rzp.io/i/once"}}

        r1 = client.post("/decide", json=_cart_body("t_idem"), headers=headers)
        r2 = client.post("/decide", json=_cart_body("t_idem"), headers=headers)

        assert r1.json()["payment_link"] == r2.json()["payment_link"] == "https://rzp.io/i/once"
        assert r2.json()["idempotent_replay"] is True
        assert mock_decide.call_count == 1, "LLM must not be called twice for a retried request"
        assert mock_rzp.call_count == 1, "Razorpay must not be called twice — this is the duplicate-charge risk"


def test_auth_blocks_unkeyed_requests_when_key_is_configured(authed_client):
    client, main = authed_client
    r = client.post("/decide", json=_cart_body("t_noauth"))
    assert r.status_code == 401

    r = client.post("/decide", json=_cart_body("t_wrongkey"), headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_auth_allows_correctly_keyed_requests(authed_client):
    client, main = authed_client
    with patch.object(main.agent_module, "decide_upsell") as mock_decide:
        mock_decide.return_value = UpsellSuggestion(should_upsell=False, reasoning="nothing to offer")
        r = client.post("/decide", json=_cart_body("t_authed"), headers={"X-API-Key": "test-key-123"})
        assert r.status_code == 200


def test_read_endpoints_stay_public_even_with_auth_configured(authed_client):
    client, main = authed_client
    assert client.get("/catalog").status_code == 200
    assert client.get("/audit/anything").status_code == 200


def test_metrics_aggregate_correctly_across_sessions(client):
    import main
    with patch.object(main.agent_module, "decide_upsell") as mock_decide, \
         patch.object(main.razorpay_client, "create_payment_link") as mock_rzp:
        mock_decide.return_value = UpsellSuggestion(should_upsell=True, sku="sku_009", discount_pct=0, reasoning="x")
        mock_rzp.return_value = {"ok": True, "payment_link": {"short_url": "https://rzp.io/i/m"}}
        client.post("/decide", json=_cart_body("m1"))

    with patch.object(main.agent_module, "decide_upsell") as mock_decide:
        mock_decide.return_value = UpsellSuggestion(should_upsell=False, reasoning="none")
        client.post("/decide", json=_cart_body("m2"))

    r = client.get("/metrics")
    m = r.json()
    assert m["total_carts_evaluated"] == 2
    assert m["upsells_suggested"] == 1
    assert m["auto_approved_and_charged"] == 1


def test_unknown_sku_in_cart_is_rejected(client):
    body = {"session_id": "t_badsku", "items": [{"sku": "sku_does_not_exist", "name": "x", "qty": 1, "price_paise": 100}]}
    r = client.post("/decide", json=body)
    assert r.status_code == 400
    assert "not in the merchant catalog" in r.json()["detail"]


def test_price_mismatch_in_cart_is_rejected(client):
    body = {"session_id": "t_badprice", "items": [{"sku": "sku_004", "name": "Mechanical Keyboard", "qty": 1, "price_paise": 1}]}
    r = client.post("/decide", json=body)
    assert r.status_code == 400
    assert "price mismatch" in r.json()["detail"]


def test_negative_quantity_rejected_at_schema_level(client):
    body = {"session_id": "t_negqty", "items": [{"sku": "sku_004", "name": "x", "qty": -1, "price_paise": 349900}]}
    r = client.post("/decide", json=body)
    assert r.status_code == 422

"""
Razorpay Test Mode client wrapper.

Wraps the official SDK with:
  - non-throwing error handling: a failed call returns a result dict,
    it never crashes the request — the caller decides what happens next
  - an optional `simulate_failure` flag used for the required failure-mode
    demo (invalid payload -> caught, logged, gracefully declined)
"""

import os
import razorpay
from dotenv import load_dotenv

load_dotenv()

_client = None


def get_client() -> razorpay.Client:
    global _client
    if _client is None:
        key_id = os.environ["RAZORPAY_KEY_ID"]
        key_secret = os.environ["RAZORPAY_KEY_SECRET"]
        _client = razorpay.Client(auth=(key_id, key_secret))
    return _client


def create_payment_link(*, amount_paise: int, description: str, session_id: str, simulate_failure: bool = False) -> dict:
    """
    Creates a Razorpay Test Mode Payment Link for the upsell item.
    Returns {"ok": True, "payment_link": {...}} on success, or
    {"ok": False, "error_type": ..., "error_message": ...} on failure.
    Never raises — the caller always gets something they can log and act on.
    """
    client = get_client()

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "description": description,
        "reference_id": f"upsell_{session_id}_{os.urandom(3).hex()}",
        "notes": {"source": "ai_upsell_agent", "session_id": session_id},
    }

    if simulate_failure:
        # Deliberately invalid amount — Razorpay's API rejects this with a
        # 400. Used to demonstrate the failure-handling path live.
        payload["amount"] = -1

    try:
        result = client.payment_link.create(payload)
        return {"ok": True, "payment_link": result}
    except razorpay.errors.BadRequestError as e:
        return {"ok": False, "error_type": "bad_request", "error_message": str(e)}
    except razorpay.errors.ServerError as e:
        return {"ok": False, "error_type": "server_error", "error_message": str(e)}
    except Exception as e:
        return {"ok": False, "error_type": "unknown", "error_message": str(e)}

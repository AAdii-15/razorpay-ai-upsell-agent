"""
Razorpay Test Mode client wrapper.

Wraps the official SDK with:
  - non-throwing error handling: a failed call returns a result dict,
    it never crashes the request — the caller decides what happens next
  - an optional `simulate_failure` flag used for the required failure-mode
    demo (invalid payload -> caught, logged, gracefully declined)
"""

import os
import hashlib
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

    # Razorpay caps reference_id at 40 characters. A raw session_id could
    # be arbitrarily long (this exact bug was caught live: a 28-char
    # session_id pushed the field to 42 chars and Razorpay correctly
    # rejected it) — hash it down to a fixed, safely-bounded length
    # instead of concatenating the raw value.
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    reference_id = f"u_{session_hash}_{os.urandom(3).hex()}"

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "description": description,
        "reference_id": reference_id,
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

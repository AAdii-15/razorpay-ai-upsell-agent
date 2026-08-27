from unittest.mock import patch
import razorpay
import razorpay_client


def test_create_payment_link_success():
    with patch.object(razorpay_client, "get_client") as mock_get:
        mock_get.return_value.payment_link.create.return_value = {
            "id": "plink_test", "short_url": "https://rzp.io/i/test",
        }
        result = razorpay_client.create_payment_link(amount_paise=14900, description="test item", session_id="s1")
        assert result["ok"] is True
        assert result["payment_link"]["short_url"] == "https://rzp.io/i/test"


def test_create_payment_link_bad_request_handled_gracefully():
    with patch.object(razorpay_client, "get_client") as mock_get:
        mock_get.return_value.payment_link.create.side_effect = razorpay.errors.BadRequestError("amount too small")
        result = razorpay_client.create_payment_link(
            amount_paise=-1, description="test", session_id="s2", simulate_failure=True,
        )
        assert result["ok"] is False
        assert result["error_type"] == "bad_request"
        assert "amount too small" in result["error_message"]


def test_create_payment_link_never_raises_on_unknown_error():
    with patch.object(razorpay_client, "get_client") as mock_get:
        mock_get.return_value.payment_link.create.side_effect = RuntimeError("something unexpected")
        result = razorpay_client.create_payment_link(amount_paise=1000, description="x", session_id="s3")
        assert result["ok"] is False
        assert result["error_type"] == "unknown"

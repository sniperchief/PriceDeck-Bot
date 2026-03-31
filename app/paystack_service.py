"""
Paystack payment integration for PriceDeck
Handles payment initialization and webhook verification
"""

import logging
import hmac
import hashlib
import httpx
from typing import Dict, Any, Optional
from app.config import PAYSTACK_SECRET_KEY

logger = logging.getLogger(__name__)

PAYSTACK_BASE_URL = "https://api.paystack.co"


async def initialize_payment(
    email: str,
    amount: int,
    reference: str,
    metadata: Dict[str, Any] = None,
    callback_url: str = None
) -> Dict[str, Any]:
    """
    Initialize a Paystack payment transaction

    Args:
        email: Customer email
        amount: Amount in kobo (100 kobo = 1 Naira)
        reference: Unique transaction reference
        metadata: Additional data to attach
        callback_url: URL to redirect after payment

    Returns:
        Paystack response with authorization_url
    """
    try:
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "email": email,
            "amount": amount,
            "reference": reference,
            "currency": "NGN",
            "metadata": metadata or {}
        }

        if callback_url:
            payload["callback_url"] = callback_url

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYSTACK_BASE_URL}/transaction/initialize",
                headers=headers,
                json=payload,
                timeout=30.0
            )

            result = response.json()
            logger.info(f"Paystack initialize response: {result}")
            return result

    except Exception as e:
        logger.error(f"Paystack initialization error: {e}")
        return {"status": False, "message": str(e)}


async def verify_payment(reference: str) -> Dict[str, Any]:
    """
    Verify a Paystack payment

    Args:
        reference: Transaction reference

    Returns:
        Paystack verification response
    """
    try:
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
                headers=headers,
                timeout=30.0
            )

            return response.json()

    except Exception as e:
        logger.error(f"Paystack verification error: {e}")
        return {"status": False, "message": str(e)}


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify Paystack webhook signature

    Args:
        payload: Raw request body
        signature: x-paystack-signature header

    Returns:
        True if signature is valid
    """
    if not PAYSTACK_SECRET_KEY:
        logger.error("PAYSTACK_SECRET_KEY not configured")
        return False

    if not signature:
        logger.warning("No signature provided in webhook")
        return False

    expected = hmac.new(
        PAYSTACK_SECRET_KEY.encode(),
        payload,
        hashlib.sha512
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


async def initiate_refund(
    reference: str,
    amount: Optional[int] = None,
    reason: str = "Order cancelled"
) -> Dict[str, Any]:
    """
    Initiate a refund for a transaction

    Args:
        reference: Transaction reference
        amount: Amount to refund in kobo (optional, full refund if not specified)
        reason: Reason for refund

    Returns:
        Paystack refund response
    """
    try:
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "transaction": reference,
            "merchant_note": reason
        }

        if amount:
            payload["amount"] = amount

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYSTACK_BASE_URL}/refund",
                headers=headers,
                json=payload,
                timeout=30.0
            )

            result = response.json()
            logger.info(f"Paystack refund response: {result}")
            return result

    except Exception as e:
        logger.error(f"Paystack refund error: {e}")
        return {"status": False, "message": str(e)}

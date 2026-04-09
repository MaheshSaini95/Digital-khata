"""
services/twilio_service.py - Send outbound WhatsApp messages via Twilio
"""
from __future__ import annotations
import logging
from twilio.rest import Client
from config import Config

logger = logging.getLogger(__name__)

_twilio: Client | None = None

def _get_client() -> Client:
    global _twilio
    if _twilio is None:
        _twilio = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
    return _twilio


def send_whatsapp_message(to_number: str, body: str) -> str:
    """
    Send a WhatsApp message to a number.
    to_number can be raw (+919...) or whatsapp:+919...
    Returns message SID.
    """
    client = _get_client()
    if not to_number.startswith("whatsapp:"):
        to_number = f"whatsapp:{to_number}"

    message = client.messages.create(
        from_=Config.TWILIO_WHATSAPP_FROM,
        to=to_number,
        body=body,
    )
    logger.info(f"Sent WhatsApp to {to_number}: {message.sid}")
    return message.sid

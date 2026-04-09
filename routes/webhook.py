"""
routes/webhook.py - Twilio WhatsApp webhook
Fixed for twilio>=9.0.0
"""
from __future__ import annotations
import logging
from flask import Blueprint, request, Response
from config import Config
from services.whatsapp_handler import handle_message

logger = logging.getLogger(__name__)
webhook_bp = Blueprint("webhook", __name__)


def _validate_twilio(req) -> bool:
    """Validate that request is genuinely from Twilio."""
    if Config.DEBUG:
        return True  # Skip in dev mode
    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(Config.TWILIO_AUTH_TOKEN)
        signature = req.headers.get("X-Twilio-Signature", "")
        return validator.validate(req.url, req.form, signature)
    except Exception as e:
        logger.error(f"Twilio validation error: {e}")
        return False


@webhook_bp.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Receive incoming WhatsApp messages from Twilio."""
    if not _validate_twilio(request):
        logger.warning("Invalid Twilio signature")
        return Response("Forbidden", status=403)

    from_number = request.form.get("From", "")
    body        = request.form.get("Body", "").strip()
    num_media   = int(request.form.get("NumMedia", 0))

    media_url = None
    if num_media > 0:
        media_url = request.form.get("MediaUrl0")

    logger.info(f"Incoming from {from_number}: {body!r} | media={bool(media_url)}")

    try:
        reply = handle_message(from_number, body, media_url=media_url)
        logger.info(f"Reply: {reply[:80]!r}...")
    except Exception as e:
        logger.exception(f"Handler error: {e}")
        reply = "Sorry, something went wrong. Please reply *menu* to try again."

    # Build TwiML response manually — works with ALL twilio versions
    # Escape special XML characters in reply
    safe_reply = reply.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{safe_reply}</Message>
</Response>"""

    logger.info(f"Sending TwiML response")
    return Response(twiml, mimetype="text/xml", status=200)


@webhook_bp.route("/webhook/status", methods=["POST"])
def message_status():
    """Handle Twilio delivery status callbacks."""
    sid    = request.form.get("MessageSid")
    status = request.form.get("MessageStatus")
    logger.debug(f"Message {sid} status: {status}")
    return Response("", status=204)


@webhook_bp.route("/webhook/test", methods=["GET"])
def test_webhook():
    """Quick test endpoint — visit in browser to check bot response."""
    try:
        reply = handle_message("whatsapp:+919509200933", "hi")
        return Response(f"<pre>{reply}</pre>", mimetype="text/html")
    except Exception as e:
        return Response(f"Error: {e}", status=500)

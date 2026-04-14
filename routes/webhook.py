"""
routes/webhook.py
Evolution API webhook handler.
Replaces Twilio webhook completely.

Evolution API sends webhooks as JSON POST requests.
We parse them and route to the same business logic (handle_message).
"""
from __future__ import annotations
import logging
import hmac
import hashlib
from flask import Blueprint, request, Response, jsonify
from config import Config
from services.whatsapp_handler import handle_message
from services.evolution_service import send_text_message

logger = logging.getLogger(__name__)
webhook_bp = Blueprint("webhook", __name__)


def _extract_message(data: dict) -> tuple[str, str, str | None]:
    """
    Extract (from_number, message_body, media_url) from Evolution API webhook payload.

    Evolution API webhook format:
    {
      "event": "messages.upsert",
      "instance": "default",
      "data": {
        "key": { "remoteJid": "919509200933@s.whatsapp.net", "fromMe": false },
        "message": {
          "conversation": "hi",
          "imageMessage": { "url": "...", "caption": "..." },
          "audioMessage": { "url": "..." }
        },
        "messageType": "conversation",
        "pushName": "Mahesh"
      }
    }
    """
    try:
        event = data.get("event", "")

        # Only process incoming messages
        if event not in ("messages.upsert", "message", "messages.update"):
            return "", "", None

        msg_data = data.get("data", {})
        if not msg_data:
            # Some versions wrap differently
            msg_data = data

        key = msg_data.get("key", {})

        # Skip messages sent by us (fromMe=true)
        if key.get("fromMe", False):
            return "", "", None

        # Extract sender number
        remote_jid = key.get("remoteJid", "")
        # Format: 919509200933@s.whatsapp.net → +919509200933
        from_number = remote_jid.replace("@s.whatsapp.net", "").replace("@c.us", "")
        if from_number and not from_number.startswith("+"):
            from_number = "+" + from_number

        # Extract message text
        message_obj = msg_data.get("message", {})
        body = (
            message_obj.get("conversation")
            or message_obj.get("extendedTextMessage", {}).get("text")
            or message_obj.get("imageMessage", {}).get("caption")
            or ""
        ).strip()

        # Extract media URL if any
        media_url = None
        if "audioMessage" in message_obj:
            media_url = message_obj["audioMessage"].get("url")
        elif "imageMessage" in message_obj:
            media_url = message_obj["imageMessage"].get("url")

        return from_number, body, media_url

    except Exception as e:
        logger.error(f"Error extracting message: {e}")
        return "", "", None


def _validate_webhook(req) -> bool:
    """
    Validate that webhook request is from Evolution API.
    Evolution API can send a secret token in headers.
    """
    if Config.DEBUG:
        return True

    # Check secret token if configured
    secret = getattr(Config, "EVOLUTION_WEBHOOK_SECRET", "")
    if not secret:
        return True  # No secret configured — accept all

    token = req.headers.get("X-Evolution-Token", "") or req.headers.get("Authorization", "")
    if token == secret or token == f"Bearer {secret}":
        return True

    logger.warning("Invalid Evolution webhook token")
    return False


@webhook_bp.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """
    Main webhook endpoint for Evolution API.
    Receives incoming WhatsApp messages and processes them.
    """
    if not _validate_webhook(request):
        return Response("Forbidden", status=403)

    # Evolution API sends JSON
    data = request.get_json(silent=True) or {}

    if not data:
        logger.warning("Empty webhook payload received")
        return jsonify({"status": "ok"}), 200

    logger.debug(f"Webhook event: {data.get('event', 'unknown')}")

    from_number, body, media_url = _extract_message(data)

    # Skip if no valid message extracted
    if not from_number:
        return jsonify({"status": "ok"}), 200

    logger.info(f"Incoming from {from_number}: {body!r} | media={bool(media_url)}")

    # Process voice message if audio
    if media_url and not body:
        try:
            from services.voice import transcribe_from_url
            body = transcribe_from_url(media_url)
            logger.info(f"Voice transcribed: {body!r}")
        except Exception as e:
            logger.error(f"Voice transcription failed: {e}")
            body = ""

    if not body and not media_url:
        return jsonify({"status": "ok"}), 200

    try:
        reply = handle_message(from_number, body, media_url=media_url)
        logger.info(f"Reply generated: {reply[:80]!r}...")
    except Exception as e:
        logger.exception(f"Handler error: {e}")
        reply = "Sorry, something went wrong. Please reply *menu* to try again."

    # Send reply via Evolution API (async — non-blocking)
    try:
        import threading
        threading.Thread(
            target=send_text_message,
            args=(from_number, reply),
            daemon=True
        ).start()
    except Exception as e:
        logger.error(f"Failed to send reply: {e}")

    # Evolution API just needs 200 OK — no TwiML needed
    return jsonify({"status": "ok", "replied": True}), 200


@webhook_bp.route("/webhook/status", methods=["POST"])
def message_status():
    """Handle Evolution API delivery status events."""
    data = request.get_json(silent=True) or {}
    event = data.get("event", "")
    logger.debug(f"Status event: {event}")
    return jsonify({"status": "ok"}), 200


@webhook_bp.route("/webhook/test", methods=["GET"])
def test_webhook():
    """Test endpoint — visit in browser to verify bot works."""
    try:
        # Use your registered number for testing
        test_number = getattr(Config, "TEST_PHONE", "+919509200933")
        reply = handle_message(test_number, "hi")
        return Response(
            f"<pre style='font-family:monospace;padding:20px'>"
            f"<b>Bot Response:</b>\n\n{reply}"
            f"</pre>",
            mimetype="text/html"
        )
    except Exception as e:
        return Response(f"Error: {e}", status=500)


@webhook_bp.route("/webhook/connection", methods=["GET"])
def check_connection():
    """Check Evolution API connection status."""
    from services.evolution_service import check_connection
    connected = check_connection()
    status = "connected" if connected else "disconnected"
    code = 200 if connected else 503
    return jsonify({"status": status, "evolution_api": connected}), code

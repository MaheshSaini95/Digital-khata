"""
services/evolution_service.py
Evolution API messaging layer — drop-in replacement for Twilio.

Evolution API runs on your own server (WhatsApp Web based).
No per-message charges. No sandbox restrictions.
Any phone number can receive messages immediately.
"""
from __future__ import annotations
import time
import logging
import requests
from config import Config

logger = logging.getLogger(__name__)

# ── Rate limiting to avoid WhatsApp ban ───────────────────────
# WhatsApp bans accounts that send too fast.
# We keep a simple delay between messages.
_last_sent_at: float = 0.0
MIN_DELAY_SECONDS: float = 1.2   # min gap between messages
BATCH_DELAY_SECONDS: float = 2.5  # gap when sending to multiple numbers


def _normalize_number(number: str) -> str:
    """
    Convert any number format to Evolution API format.
    Evolution API expects: 919509200933 (no + or whatsapp: prefix)
    """
    number = number.strip()
    number = number.replace("whatsapp:", "")
    number = number.replace("+", "")
    number = number.replace(" ", "")
    number = number.replace("-", "")
    # Ensure India country code
    if number.startswith("0"):
        number = "91" + number[1:]
    elif len(number) == 10:
        number = "91" + number
    return number


def _get_headers() -> dict:
    """Build Evolution API auth headers."""
    headers = {"Content-Type": "application/json"}
    api_key = getattr(Config, "EVOLUTION_API_KEY", "")
    if api_key:
        headers["apikey"] = api_key
    return headers


def _respect_rate_limit(delay: float = MIN_DELAY_SECONDS):
    """Sleep if needed to avoid WhatsApp ban."""
    global _last_sent_at
    elapsed = time.monotonic() - _last_sent_at
    if elapsed < delay:
        sleep_time = delay - elapsed
        logger.debug(f"Rate limit: sleeping {sleep_time:.2f}s")
        time.sleep(sleep_time)
    _last_sent_at = time.monotonic()


def send_text_message(to_number: str, text: str,
                      delay: float = MIN_DELAY_SECONDS) -> dict:
    """
    Send a plain text WhatsApp message via Evolution API.

    Args:
        to_number: recipient number (any format)
        text: message content
        delay: seconds to wait before sending (rate limiting)

    Returns:
        dict with success/error info
    """
    _respect_rate_limit(delay)

    number = _normalize_number(to_number)
    instance = getattr(Config, "EVOLUTION_INSTANCE", "default")
    base_url = getattr(Config, "EVOLUTION_API_URL", "http://localhost:3000")

    url = f"{base_url}/message/sendText/{instance}"

    payload = {
        "number": number,
        "text": text,
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            headers=_get_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"Message sent to {number}: {result.get('key', {}).get('id', 'ok')}")
        return {"success": True, "data": result, "number": number}

    except requests.exceptions.ConnectionError:
        logger.error(f"Evolution API not reachable at {base_url}")
        return {"success": False, "error": "Evolution API not reachable", "number": number}
    except requests.exceptions.HTTPError as e:
        logger.error(f"Evolution API HTTP error: {e.response.status_code} — {e.response.text}")
        return {"success": False, "error": str(e), "number": number}
    except Exception as e:
        logger.error(f"Evolution API error: {e}")
        return {"success": False, "error": str(e), "number": number}


def send_image_message(to_number: str, image_url: str,
                       caption: str = "", delay: float = BATCH_DELAY_SECONDS) -> dict:
    """
    Send an image message via Evolution API.

    Args:
        to_number: recipient number
        image_url: public URL of the image
        caption: optional caption text
    """
    _respect_rate_limit(delay)

    number = _normalize_number(to_number)
    instance = getattr(Config, "EVOLUTION_INSTANCE", "default")
    base_url = getattr(Config, "EVOLUTION_API_URL", "http://localhost:3000")

    url = f"{base_url}/message/sendMedia/{instance}"

    payload = {
        "number": number,
        "mediatype": "image",
        "media": image_url,
        "caption": caption,
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            headers=_get_headers(),
            timeout=20,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"Image sent to {number}")
        return {"success": True, "data": result, "number": number}

    except Exception as e:
        logger.error(f"Image send failed: {e} — falling back to text")
        # Fallback to text if image fails
        return send_text_message(to_number, caption or "Bill sent", delay=0)


def send_whatsapp_message(to_number: str, body: str) -> str:
    """
    Drop-in replacement for twilio_service.send_whatsapp_message()
    Same signature — returns message ID string.
    """
    result = send_text_message(to_number, body)
    if result["success"]:
        return result.get("data", {}).get("key", {}).get("id", "sent")
    raise Exception(f"Failed to send message: {result.get('error')}")


def send_bulk_messages(recipients: list[dict], delay: float = BATCH_DELAY_SECONDS):
    """
    Send messages to multiple recipients with delays between each.

    Args:
        recipients: list of {"number": "...", "text": "..."}
        delay: seconds between each message
    """
    results = []
    for i, r in enumerate(recipients):
        logger.info(f"Sending bulk message {i+1}/{len(recipients)} to {r['number']}")
        result = send_text_message(r["number"], r["text"], delay=delay)
        results.append(result)
    return results


def check_connection() -> bool:
    """Check if Evolution API is running and connected."""
    base_url = getattr(Config, "EVOLUTION_API_URL", "http://localhost:3000")
    instance = getattr(Config, "EVOLUTION_INSTANCE", "default")
    try:
        resp = requests.get(
            f"{base_url}/instance/connectionState/{instance}",
            headers=_get_headers(),
            timeout=5,
        )
        data = resp.json()
        state = data.get("instance", {}).get("state", "")
        connected = state == "open"
        logger.info(f"Evolution API connection: {state}")
        return connected
    except Exception as e:
        logger.error(f"Evolution API check failed: {e}")
        return False

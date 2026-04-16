"""
services/registration.py
Professional self-service registration flow via WhatsApp.

Flow:
  1. New number sends any message
  2. Bot asks: "Would you like to register? Reply YES"
  3. Bot asks: "Enter your full name"
  4. Bot asks: "Enter your business/shop name"
  5. Bot asks: "Enter your city/address"
  6. Bot sends OTP to verify the number
  7. User enters OTP → account created → welcome message sent
"""
from __future__ import annotations
import random
import logging
from datetime import datetime, timezone, timedelta
from services.database import get_db
from services import session as sess

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# OTP HELPERS
# ─────────────────────────────────────────────────────────────

def generate_otp() -> str:
    """Generate a 6-digit OTP."""
    return str(random.randint(100000, 999999))


def save_otp(number: str, otp: str, context: dict) -> None:
    """Save OTP and registration context to DB."""
    db = get_db()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    db.table("pending_registrations").upsert({
        "whatsapp_number": number,
        "otp": otp,
        "otp_expires": expires,
        "context": context,
    }, on_conflict="whatsapp_number").execute()


def verify_otp(number: str, entered_otp: str) -> dict | None:
    """
    Verify OTP. Returns registration context if valid, None if invalid/expired.
    """
    db = get_db()
    try:
        res = db.table("pending_registrations").select("*") \
            .eq("whatsapp_number", number) \
            .limit(1).execute()

        if not res.data:
            return None

        row = res.data[0]

        # Check expiry
        expires = datetime.fromisoformat(row["otp_expires"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            logger.info(f"OTP expired for {number}")
            return None

        # Check OTP
        if row["otp"] != entered_otp.strip():
            logger.info(f"Wrong OTP for {number}")
            return None

        # Valid — delete pending record
        db.table("pending_registrations").delete() \
            .eq("whatsapp_number", number).execute()

        return row["context"]

    except Exception as e:
        logger.error(f"OTP verify error: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# COMPLETE REGISTRATION
# ─────────────────────────────────────────────────────────────

def complete_registration(number: str, context: dict) -> dict:
    """
    Create client account after OTP verified.
    Returns the new client record.
    """
    db = get_db()

    # Create client
    client_data = {
        "whatsapp_number": number,
        "name": context.get("owner_name", ""),
        "business_name": context.get("business_name", ""),
        "owner_name": context.get("owner_name", ""),
        "address": context.get("address", ""),
        "is_active": True,
        "is_verified": True,
        "onboarding_step": "complete",
        "plan": "free",
    }

    res = db.table("clients").upsert(
        client_data, on_conflict="whatsapp_number"
    ).execute()
    client = res.data[0]

    # Add to client_numbers table
    try:
        db.table("client_numbers").upsert({
            "client_id": client["id"],
            "number": number,
            "label": "primary",
        }, on_conflict="number").execute()
    except Exception as e:
        logger.error(f"client_numbers insert error: {e}")

    logger.info(f"Registration complete for {number}: {client['business_name']}")
    return client


# ─────────────────────────────────────────────────────────────
# MULTI-NUMBER: ADD SECONDARY NUMBER
# ─────────────────────────────────────────────────────────────

def add_secondary_number(client_id: str, new_number: str,
                          label: str = "secondary") -> bool:
    """Add a secondary WhatsApp number to an existing client."""
    db = get_db()

    # Check if number already belongs to another client
    existing = db.table("client_numbers").select("client_id") \
        .eq("number", new_number).limit(1).execute()

    if existing.data:
        if existing.data[0]["client_id"] != client_id:
            logger.warning(f"Number {new_number} already registered to another client")
            return False
        return True  # Already registered to this client

    try:
        db.table("client_numbers").insert({
            "client_id": client_id,
            "number": new_number,
            "label": label,
        }).execute()
        logger.info(f"Added {new_number} as {label} for client {client_id}")
        return True
    except Exception as e:
        logger.error(f"Add number error: {e}")
        return False


def remove_secondary_number(client_id: str, number: str) -> bool:
    """Remove a secondary number (cannot remove primary)."""
    db = get_db()
    try:
        res = db.table("client_numbers").delete() \
            .eq("client_id", client_id) \
            .eq("number", number) \
            .eq("label", "secondary").execute()
        return True
    except Exception as e:
        logger.error(f"Remove number error: {e}")
        return False


def get_client_numbers(client_id: str) -> list[dict]:
    """Get all numbers for a client."""
    db = get_db()
    res = db.table("client_numbers").select("*") \
        .eq("client_id", client_id).execute()
    return res.data or []

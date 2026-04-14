"""
config.py - Central configuration
Supports both Evolution API (new) and Twilio (legacy, optional)
"""
import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    # ── Supabase (unchanged) ──────────────────────────────────
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    # ── Evolution API (NEW — replaces Twilio) ─────────────────
    EVOLUTION_API_URL:      str = os.getenv("EVOLUTION_API_URL", "http://localhost:3000")
    EVOLUTION_INSTANCE:     str = os.getenv("EVOLUTION_INSTANCE", "default")
    EVOLUTION_API_KEY:      str = os.getenv("EVOLUTION_API_KEY", "")
    EVOLUTION_WEBHOOK_SECRET: str = os.getenv("EVOLUTION_WEBHOOK_SECRET", "")

    # ── Twilio (LEGACY — keep for backward compat, optional) ──
    TWILIO_ACCOUNT_SID:   str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN:    str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_WHATSAPP_FROM: str = os.getenv("TWILIO_WHATSAPP_FROM", "")

    # ── OpenAI (Whisper voice transcription) ──────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # ── App ───────────────────────────────────────────────────
    SECRET_KEY:          str  = os.getenv("SECRET_KEY", "change-me")
    DEBUG:               bool = os.getenv("DEBUG", "false").lower() == "true"
    SESSION_TTL_MINUTES: int  = int(os.getenv("SESSION_TTL_MINUTES", "30"))
    TEST_PHONE:          str  = os.getenv("TEST_PHONE", "+919509200933")

    # ── Bill / Shop Details ───────────────────────────────────
    OWNER_NAME:   str = os.getenv("OWNER_NAME",   "Shop Owner")
    OWNER_PHONE:  str = os.getenv("OWNER_PHONE",  "")
    SHOP_ADDRESS: str = os.getenv("SHOP_ADDRESS", "Jaipur, Rajasthan")

    # ── ImgBB (free image hosting for bill images) ────────────
    IMGBB_API_KEY: str = os.getenv("IMGBB_API_KEY", "")

    # ── Messaging backend selector ────────────────────────────
    # "evolution" (default) or "twilio" (legacy)
    MESSAGING_BACKEND: str = os.getenv("MESSAGING_BACKEND", "evolution")

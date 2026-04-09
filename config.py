"""config.py - Central configuration management"""
import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    # ── Supabase ──────────────────────────────────────────────
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    # ── Twilio ────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID:   str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN:    str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_WHATSAPP_FROM: str = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    # ── OpenAI ────────────────────────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # ── App ───────────────────────────────────────────────────
    SECRET_KEY:          str = os.getenv("SECRET_KEY", "change-me")
    DEBUG:               bool = os.getenv("DEBUG", "false").lower() == "true"
    SESSION_TTL_MINUTES: int  = int(os.getenv("SESSION_TTL_MINUTES", "30"))

    # ── ImgBB (free image hosting) ────────────────────────────
    IMGBB_API_KEY: str = os.getenv("IMGBB_API_KEY", "")

    # ── Shop Details (for bill) ───────────────────────────────
    OWNER_NAME:   str = os.getenv("OWNER_NAME",   "Heerlal Saini, Ramesh Saini")
    OWNER_PHONE:  str = os.getenv("OWNER_PHONE",  "7014764661")
    SHOP_ADDRESS: str = os.getenv("SHOP_ADDRESS", "C-93, Heerlal Saini, Ramesh Saini, Muhana Mandi, Jaipur")

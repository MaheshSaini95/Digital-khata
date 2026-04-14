"""
services/voice.py
Voice message processing.
Works with both Twilio media URLs and Evolution API media URLs.
Downloads audio → converts to WAV → transcribes with OpenAI Whisper.
"""
from __future__ import annotations
import os
import logging
import tempfile
import subprocess
import requests
from openai import OpenAI
from config import Config

logger = logging.getLogger(__name__)

_openai: OpenAI | None = None


def _get_openai() -> OpenAI:
    global _openai
    if _openai is None:
        _openai = OpenAI(api_key=Config.OPENAI_API_KEY)
    return _openai


def download_audio(media_url: str, auth: tuple | None = None) -> str:
    """
    Download audio from any URL.
    auth: optional (username, password) tuple for Twilio URLs.
    Returns path to downloaded file.
    """
    resp = requests.get(media_url, auth=auth, timeout=30)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "mpeg" in content_type or "mp3" in content_type:
        suffix = ".mp3"
    elif "mp4" in content_type or "m4a" in content_type:
        suffix = ".m4a"
    elif "opus" in content_type:
        suffix = ".opus"
    else:
        suffix = ".ogg"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(resp.content)
    tmp.close()
    return tmp.name


def convert_to_wav(input_path: str) -> str:
    """Convert any audio file to 16kHz mono WAV using ffmpeg."""
    output_path = input_path.rsplit(".", 1)[0] + ".wav"
    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-ar", "16000", "-ac", "1", "-f", "wav", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {result.stderr}")
    return output_path


def transcribe(audio_path: str, language: str = "hi") -> str:
    """Transcribe audio using OpenAI Whisper."""
    client = _get_openai()
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language=language,
            prompt=(
                "Shop owner recording customer ledger entry. "
                "Hindi/English mix. Items, quantities, amounts, payment."
            ),
        )
    return response.text.strip()


def process_voice_message(media_url: str,
                           twilio_auth: tuple | None = None) -> str:
    """
    Full pipeline: download → convert → transcribe.
    Works with Twilio URLs (pass twilio_auth) or Evolution API URLs (no auth).
    """
    raw_path = None
    wav_path = None
    try:
        raw_path = download_audio(media_url, auth=twilio_auth)
        wav_path = convert_to_wav(raw_path)
        text = transcribe(wav_path)
        logger.info(f"Voice transcribed: {text!r}")
        return text
    finally:
        for p in (raw_path, wav_path):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def transcribe_from_url(media_url: str) -> str:
    """
    Convenience function for Evolution API voice messages.
    No auth needed — Evolution API serves media directly.
    """
    return process_voice_message(media_url, twilio_auth=None)

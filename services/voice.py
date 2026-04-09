"""
services/voice.py - Download Twilio audio, convert OGG→WAV, transcribe with Whisper
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


def download_audio(media_url: str, twilio_sid: str, twilio_auth: str) -> str:
    """
    Download audio from Twilio MediaUrl.
    Returns path to downloaded file.
    """
    resp = requests.get(
        media_url,
        auth=(twilio_sid, twilio_auth),
        timeout=30,
    )
    resp.raise_for_status()

    suffix = ".ogg"
    content_type = resp.headers.get("Content-Type", "")
    if "mpeg" in content_type or "mp3" in content_type:
        suffix = ".mp3"
    elif "mp4" in content_type or "m4a" in content_type:
        suffix = ".m4a"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(resp.content)
    tmp.close()
    logger.debug(f"Downloaded audio to {tmp.name} ({len(resp.content)} bytes)")
    return tmp.name


def convert_to_wav(input_path: str) -> str:
    """
    Convert any audio file to 16-kHz mono WAV using ffmpeg.
    Returns path to WAV file.
    """
    output_path = input_path.rsplit(".", 1)[0] + ".wav"
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", "-f", "wav", output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg error: {result.stderr}")
        raise RuntimeError(f"Audio conversion failed: {result.stderr}")
    return output_path


def transcribe(audio_path: str, language: str = "hi") -> str:
    """
    Transcribe audio file using OpenAI Whisper.
    Supports Hindi (hi) and English (en).
    Returns transcribed text.
    """
    client = _get_openai()
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language=language,
            prompt=(
                "This is a shopkeeper recording a customer ledger entry. "
                "Names and items in Hindi/English: milk, bread, rice, dal, "
                "payment, due, rupees, udhaar."
            ),
        )
    return response.text.strip()


def process_voice_message(media_url: str) -> str:
    """
    Full pipeline: download → convert → transcribe.
    Returns transcribed text.
    """
    raw_path = None
    wav_path = None
    try:
        raw_path = download_audio(
            media_url,
            Config.TWILIO_ACCOUNT_SID,
            Config.TWILIO_AUTH_TOKEN,
        )
        wav_path = convert_to_wav(raw_path)
        text = transcribe(wav_path)
        logger.info(f"Transcribed voice: {text!r}")
        return text
    finally:
        for p in (raw_path, wav_path):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass

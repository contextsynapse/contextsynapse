"""Audio Transcription Connector — transcribes earnings calls and analyst audio.

Supports multiple backends:
  1. Ollama whisper model (local, free — if running)
  2. Groq Whisper API (fast, free tier — needs GROQ_API_KEY)
  3. OpenAI Whisper API (paid — needs OPENAI_API_KEY)

The transcript is then fed into the existing smart_ingest pipeline.

Usage:
    transcript = transcribe_audio("earnings_call.mp3")
    # → text → smart_ingest → entities extracted → sensor fusion updated
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def transcribe_audio(
    file_path: str,
    backend: str = "auto",
    language: str = "en",
) -> Optional[str]:
    """Transcribe an audio file to text.

    Args:
        file_path: Path to audio file (MP3, WAV, M4A, WebM)
        backend: "auto" | "groq" | "openai" | "ollama"
        language: Language code (default "en")

    Returns:
        Transcript text or None if transcription failed.
    """
    if not os.path.isfile(file_path):
        logger.error("[AUDIO] File not found: %s", file_path)
        return None

    if backend == "auto":
        # Try backends in order of preference
        for b in ["groq", "openai", "ollama"]:
            result = transcribe_audio(file_path, backend=b, language=language)
            if result:
                return result
        logger.warning("[AUDIO] No transcription backend available")
        return None

    if backend == "groq":
        return _transcribe_groq(file_path, language)
    elif backend == "openai":
        return _transcribe_openai(file_path, language)
    elif backend == "ollama":
        return _transcribe_ollama(file_path, language)
    else:
        logger.warning("[AUDIO] Unknown backend: %s", backend)
        return None


def _transcribe_groq(file_path: str, language: str) -> Optional[str]:
    """Transcribe using Groq Whisper API (fast, free tier available)."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.debug("[AUDIO] GROQ_API_KEY not set, skipping Groq backend")
        return None

    try:
        from groq import Groq
        client = Groq(api_key=api_key)

        with open(file_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                file=(Path(file_path).name, f),
                model="whisper-large-v3",
                language=language,
                response_format="text",
            )
        logger.info("[AUDIO] Groq transcription complete: %d chars", len(transcription))
        return transcription
    except ImportError:
        logger.debug("[AUDIO] groq package not installed")
        return None
    except Exception as e:
        logger.warning("[AUDIO] Groq transcription failed: %s", e)
        return None


def _transcribe_openai(file_path: str, language: str) -> Optional[str]:
    """Transcribe using OpenAI Whisper API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.debug("[AUDIO] OPENAI_API_KEY not set, skipping OpenAI backend")
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        with open(file_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                file=f,
                model="whisper-1",
                language=language,
                response_format="text",
            )
        logger.info("[AUDIO] OpenAI transcription complete: %d chars", len(transcription))
        return transcription
    except ImportError:
        logger.debug("[AUDIO] openai package not installed")
        return None
    except Exception as e:
        logger.warning("[AUDIO] OpenAI transcription failed: %s", e)
        return None


def _transcribe_ollama(file_path: str, language: str) -> Optional[str]:
    """Transcribe using local Ollama with whisper model."""
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

    try:
        import requests
        # Check if whisper model is available
        resp = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if resp.status_code != 200:
            return None

        models = [m.get("name", "") for m in resp.json().get("models", [])]
        whisper_model = None
        for m in models:
            if "whisper" in m.lower():
                whisper_model = m
                break

        if not whisper_model:
            logger.debug("[AUDIO] No whisper model found in Ollama")
            return None

        # Ollama doesn't natively support audio transcription via API
        # This would need a custom integration
        logger.debug("[AUDIO] Ollama whisper transcription not yet implemented")
        return None
    except Exception as e:
        logger.debug("[AUDIO] Ollama check failed: %s", e)
        return None


def get_available_backends() -> list:
    """Check which transcription backends are available."""
    backends = []

    if os.environ.get("GROQ_API_KEY"):
        try:
            import groq
            backends.append({"name": "groq", "model": "whisper-large-v3", "status": "available"})
        except ImportError:
            backends.append({"name": "groq", "model": "whisper-large-v3", "status": "package_missing"})

    if os.environ.get("OPENAI_API_KEY"):
        try:
            import openai
            backends.append({"name": "openai", "model": "whisper-1", "status": "available"})
        except ImportError:
            backends.append({"name": "openai", "model": "whisper-1", "status": "package_missing"})

    backends.append({"name": "ollama", "model": "whisper", "status": "experimental"})

    return backends

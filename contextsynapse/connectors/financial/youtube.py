"""YouTube Transcript Connector — extracts transcripts from YouTube videos.

Uses youtube-transcript-api (free, no API key needed).
Most financial videos have auto-generated captions.

Usage:
    connector = YouTubeConnector(config)
    docs = connector.poll()  # fetches transcript for configured video IDs

Or directly:
    transcript = YouTubeConnector.extract_transcript("https://youtube.com/watch?v=xxxx")
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from ..base import BaseConnector, ConnectorConfig, ConnectorDocument

logger = logging.getLogger(__name__)

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None


def extract_video_id(url: str) -> Optional[str]:
    """Extract video ID from various YouTube URL formats."""
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    # Maybe it's already a video ID
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url):
        return url
    return None


class YouTubeConnector(BaseConnector):
    """Extracts transcripts from YouTube videos."""

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self._urls: List[str] = config.config.get("urls", [])
        self._languages: List[str] = config.config.get("languages", ["en", "hi"])

    def poll(self) -> List[ConnectorDocument]:
        """Fetch transcripts for configured YouTube URLs."""
        docs = []
        for url in self._urls:
            transcript = self.extract_transcript(url)
            if transcript:
                docs.append(transcript)
        return self._dedup(docs)

    def extract_transcript(self, url: str) -> Optional[ConnectorDocument]:
        """Extract transcript from a single YouTube URL."""
        video_id = extract_video_id(url)
        if not video_id:
            logger.warning("[YOUTUBE] Could not extract video ID from: %s", url)
            return None

        text = self._get_transcript_text(video_id)
        if not text:
            return None

        return ConnectorDocument(
            title=f"YouTube Transcript: {video_id}",
            content=text,
            url=f"https://www.youtube.com/watch?v={video_id}",
            source="youtube",
            tags=["youtube", "transcript", "video"],
            doc_type="transcript",
            metadata={
                "video_id": video_id,
                "original_url": url,
                "transcript_length": len(text),
            },
        )

    def _get_transcript_text(self, video_id: str) -> str:
        """Get transcript text for a video ID."""
        if YouTubeTranscriptApi is None:
            logger.warning("[YOUTUBE] youtube-transcript-api not installed. Install: pip install youtube-transcript-api")
            return ""

        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(
                video_id, languages=self._languages
            )
            # Join all segments into one text
            return " ".join(entry["text"] for entry in transcript_list)
        except Exception as e:
            logger.warning("[YOUTUBE] Failed to get transcript for %s: %s", video_id, e)
            return ""

    def health_check(self) -> bool:
        if YouTubeTranscriptApi is None:
            return False
        return True


def transcribe_youtube(url: str, languages: List[str] = None) -> Optional[str]:
    """Convenience function — extract transcript from a YouTube URL.

    Returns transcript text or None.
    """
    if languages is None:
        languages = ["en", "hi"]

    video_id = extract_video_id(url)
    if not video_id:
        return None

    if YouTubeTranscriptApi is None:
        logger.warning("youtube-transcript-api not installed")
        return None

    # Try manual captions first, then auto-generated
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(
            video_id, languages=languages
        )
        return " ".join(entry["text"] for entry in transcript_list)
    except Exception:
        pass

    # Try auto-generated captions (a]XX format)
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        # Try generated (auto) captions in any language
        for transcript in transcript_list:
            if transcript.is_generated:
                entries = transcript.fetch()
                text = " ".join(entry["text"] for entry in entries)
                if text.strip():
                    return text
        # Try any available transcript
        for transcript in transcript_list:
            entries = transcript.fetch()
            text = " ".join(entry["text"] for entry in entries)
            if text.strip():
                return text
    except Exception as e:
        logger.warning("YouTube captions unavailable for %s: %s", video_id, e)

    # Fallback: download audio and transcribe with Whisper
    logger.info("Attempting Whisper audio transcription for %s", video_id)
    whisper_text = _whisper_fallback(url, video_id)
    if whisper_text:
        return whisper_text

    return None


def _whisper_fallback(url: str, video_id: str) -> Optional[str]:
    """Download YouTube audio and transcribe with OpenAI Whisper.

    Requires: pip install yt-dlp openai-whisper (or faster-whisper)
    Falls back gracefully if not installed.
    """
    import tempfile
    import os

    # Step 1: Download audio with yt-dlp
    audio_path = None
    try:
        import yt_dlp
        tmp_dir = tempfile.mkdtemp(prefix="yt_whisper_")
        audio_path = os.path.join(tmp_dir, f"{video_id}.mp3")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmp_dir, f"{video_id}.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",  # low quality = smaller file = faster
            }],
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the downloaded file
        if not os.path.exists(audio_path):
            for f in os.listdir(tmp_dir):
                if f.endswith((".mp3", ".m4a", ".wav", ".opus", ".webm")):
                    audio_path = os.path.join(tmp_dir, f)
                    break

        if not audio_path or not os.path.exists(audio_path):
            logger.warning("yt-dlp download produced no audio file for %s", video_id)
            return None

        logger.info("Audio downloaded: %s (%.1f MB)", audio_path,
                     os.path.getsize(audio_path) / 1e6)

    except ImportError:
        logger.info("yt-dlp not installed — Whisper fallback unavailable. Install: pip install yt-dlp")
        return None
    except Exception as e:
        logger.warning("yt-dlp download failed for %s: %s", video_id, e)
        return None

    # Step 2: Transcribe with Whisper
    transcript = None

    # Try faster-whisper first (much faster, lower memory)
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, info = model.transcribe(audio_path, language="en")
        transcript = " ".join(seg.text.strip() for seg in segments)
        logger.info("faster-whisper transcribed %s: %d chars, lang=%s",
                     video_id, len(transcript), info.language)
    except ImportError:
        pass
    except Exception as e:
        logger.warning("faster-whisper failed for %s: %s", video_id, e)

    # Try openai-whisper (slower but more common)
    if not transcript:
        try:
            import whisper
            model = whisper.load_model("base")
            result = model.transcribe(audio_path, language="en")
            transcript = result.get("text", "")
            logger.info("whisper transcribed %s: %d chars", video_id, len(transcript))
        except ImportError:
            pass
        except Exception as e:
            logger.warning("whisper failed for %s: %s", video_id, e)

    # Try OpenAI API Whisper (cloud, requires API key)
    if not transcript:
        try:
            import openai
            client = openai.OpenAI()
            with open(audio_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language="en",
                )
            transcript = result.text
            logger.info("OpenAI Whisper API transcribed %s: %d chars", video_id, len(transcript))
        except ImportError:
            pass
        except Exception as e:
            logger.warning("OpenAI Whisper API failed for %s: %s", video_id, e)

    if not transcript:
        logger.warning("All Whisper methods failed for %s. Install: pip install faster-whisper yt-dlp", video_id)

    # Cleanup
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    return transcript if transcript and transcript.strip() else None

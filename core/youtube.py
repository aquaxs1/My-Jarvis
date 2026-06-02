"""
JARVIS YouTube-Zusammenfassungen
- Transkript abrufen (youtube-transcript-api)
- Zusammenfassung generieren (über Brain)
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

YT_URL_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})'
)
CHUNK_MINUTES = 5


class YouTubeManager:
    def __init__(self):
        self._available = False
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            self._api = YouTubeTranscriptApi
            self._available = True
        except ImportError:
            logger.info("[YouTube] youtube-transcript-api nicht installiert")

    @property
    def is_available(self) -> bool:
        return self._available

    @staticmethod
    def extract_video_id(text: str) -> Optional[str]:
        match = YT_URL_PATTERN.search(text)
        return match.group(1) if match else None

    @staticmethod
    def contains_youtube_url(text: str) -> bool:
        return bool(YT_URL_PATTERN.search(text))

    def get_transcript(self, video_id: str) -> Optional[list]:
        if not self._available:
            return None
        try:
            transcript_list = self._api.list_transcripts(video_id)

            for lang in ["de", "en"]:
                try:
                    transcript = transcript_list.find_transcript([lang])
                    return transcript.fetch()
                except Exception:
                    continue

            try:
                transcript = transcript_list.find_generated_transcript(["de", "en"])
                return transcript.fetch()
            except Exception:
                pass

            for transcript in transcript_list:
                return transcript.fetch()

        except Exception as e:
            logger.error("[YouTube] Transkript abrufen fehlgeschlagen: %s", e)
            return None

    def transcript_to_text(self, transcript: list) -> str:
        return " ".join(entry["text"] for entry in transcript)

    def chunk_transcript(self, transcript: list) -> list:
        if not transcript:
            return []

        total_duration = transcript[-1]["start"] + transcript[-1].get("duration", 0)
        chunk_seconds = CHUNK_MINUTES * 60

        if total_duration <= 15 * 60:
            return [self.transcript_to_text(transcript)]

        chunks = []
        current_chunk = []
        chunk_start = 0

        for entry in transcript:
            if entry["start"] >= chunk_start + chunk_seconds and current_chunk:
                chunks.append(" ".join(e["text"] for e in current_chunk))
                current_chunk = []
                chunk_start = entry["start"]
            current_chunk.append(entry)

        if current_chunk:
            chunks.append(" ".join(e["text"] for e in current_chunk))

        return chunks

    def get_video_summary_prompt(self, video_id: str) -> Optional[str]:
        transcript = self.get_transcript(video_id)
        if not transcript:
            return None

        chunks = self.chunk_transcript(transcript)
        if not chunks:
            return None

        if len(chunks) == 1:
            return (
                f"Fasse folgendes YouTube-Video-Transkript zusammen. "
                f"Gib eine kompakte, strukturierte Zusammenfassung auf Deutsch:\n\n"
                f"{chunks[0][:8000]}"
            )

        chunk_texts = "\n\n---\n\n".join(
            f"**Abschnitt {i+1}:**\n{c[:2000]}"
            for i, c in enumerate(chunks)
        )
        return (
            f"Fasse folgendes YouTube-Video-Transkript zusammen. "
            f"Es ist in {len(chunks)} Abschnitte aufgeteilt. "
            f"Erstelle eine Gesamtzusammenfassung auf Deutsch:\n\n"
            f"{chunk_texts[:8000]}"
        )

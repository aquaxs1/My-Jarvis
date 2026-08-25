"""
My Jarvis document reader
- reads PDF, DOCX, TXT, MD
- chunking for long documents
"""
import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_CHUNK_SIZE = 3000


class DocumentReader:
    def read(self, filepath: str) -> Optional[str]:
        path = Path(filepath)
        if not path.exists():
            return None

        ext = path.suffix.lower()
        try:
            if ext == ".pdf":
                return self._read_pdf(path)
            elif ext == ".docx":
                return self._read_docx(path)
            elif ext in (".txt", ".md", ".csv", ".log", ".json", ".xml", ".html"):
                return self._read_text(path)
            else:
                return self._read_text(path)
        except Exception as e:
            logger.error("[DocReader] Error reading %s: %s", filepath, e)
            return None

    def _read_pdf(self, path: Path) -> Optional[str]:
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(str(path)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            if text_parts:
                return "\n\n".join(text_parts)
        except ImportError:
            logger.debug("[DocReader] pdfplumber is not installed, trying pypdf")
        except Exception as e:
            logger.debug("[DocReader] pdfplumber Fehler: %s", e)

        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            text_parts = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return "\n\n".join(text_parts) if text_parts else None
        except ImportError:
            logger.warning("[DocReader] Weder pdfplumber noch pypdf installiert")
            return None
        except Exception as e:
            logger.error("[DocReader] pypdf Fehler: %s", e)
            return None

    def _read_docx(self, path: Path) -> Optional[str]:
        try:
            import docx
            doc = docx.Document(str(path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs) if paragraphs else None
        except ImportError:
            logger.warning("[DocReader] python-docx nicht installiert")
            return None
        except Exception as e:
            logger.error("[DocReader] DOCX Fehler: %s", e)
            return None

    def _read_text(self, path: Path) -> Optional[str]:
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                return path.read_text(encoding=encoding)
            except (UnicodeDecodeError, ValueError):
                continue
        return None

    @staticmethod
    def chunk_text(text: str, max_size: int = MAX_CHUNK_SIZE) -> list:
        if len(text) <= max_size:
            return [text]

        chunks = []
        paragraphs = text.split("\n\n")
        current = ""

        for para in paragraphs:
            if len(current) + len(para) + 2 > max_size:
                if current:
                    chunks.append(current.strip())
                if len(para) > max_size:
                    for i in range(0, len(para), max_size):
                        chunks.append(para[i:i + max_size].strip())
                    current = ""
                else:
                    current = para
            else:
                current = current + "\n\n" + para if current else para

        if current.strip():
            chunks.append(current.strip())

        return chunks

    @staticmethod
    def get_supported_extensions() -> list:
        return [".pdf", ".docx", ".txt", ".md", ".csv", ".log", ".json", ".xml", ".html"]

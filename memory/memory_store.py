"""
My Jarvis memory store v2.8 – encrypted storage
- save_memory_kv: stores as key=value
- get_relevant_context: returns the kv format
- All JSON files encrypted at rest with Fernet (PBKDF2-derived key)
"""
import json
import hashlib
import logging
import os
import platform
import getpass
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import base64

logger = logging.getLogger("jarvis.memory")

MEMORY_DIR = Path.home() / ".jarvis" / "memory"
SALT_FILE = MEMORY_DIR / ".salt"
MAX_KV_LENGTH = 500
MAX_HISTORY_ENTRIES = 1000


def _get_machine_secret() -> bytes:
    """Derive a machine-specific secret from hostname + username."""
    hostname = platform.node()
    username = getpass.getuser()
    return f"{hostname}:{username}:jarvis-memory-v2".encode("utf-8")


def _get_or_create_salt() -> bytes:
    """Get or create a persistent salt for key derivation."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = os.urandom(16)
    SALT_FILE.write_bytes(salt)
    return salt


def _derive_fernet_key() -> bytes:
    """Derive a Fernet key from machine secret + salt via PBKDF2."""
    salt = _get_or_create_salt()
    secret = _get_machine_secret()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    key = kdf.derive(secret)
    return base64.urlsafe_b64encode(key)


# Cache the Fernet instance per-process to avoid re-deriving on every I/O
_fernet_instance: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = Fernet(_derive_fernet_key())
    return _fernet_instance


class MemoryStore:
    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.memories_file = MEMORY_DIR / "memories.json"
        self.history_file  = MEMORY_DIR / "history.json"
        self.routines_file = MEMORY_DIR / "routines.json"
        self.projects_file = MEMORY_DIR / "projects.json"
        # bug 15: guards the in-memory lists + encrypted file writes against
        # concurrent access from several threads (brain, copilot,
        # Proactive, GUI). RLock erlaubt verschachtelte Aufrufe.
        self._lock = threading.RLock()
        self._load_all()

    def _load_all(self):
        self.memories = self._load_json(self.memories_file, [])
        self.history  = self._load_json(self.history_file, [])
        self.routines = self._load_json(self.routines_file, [])
        self.projects = self._load_json(self.projects_file, [])

    def _load_json(self, path: Path, default):
        """
        Load JSON from an encrypted file. Handles migration from plaintext:
        if the file is valid plaintext JSON, read it and re-save encrypted.
        """
        if not path.exists():
            return default

        raw = path.read_bytes()
        if not raw:
            return default

        # Try decrypting first (normal path for encrypted files)
        try:
            fernet = _get_fernet()
            decrypted = fernet.decrypt(raw)
            return json.loads(decrypted.decode("utf-8"))
        except (InvalidToken, Exception):
            pass

        # Fallback: try reading as plaintext JSON (migration from v1.x)
        try:
            data = json.loads(raw.decode("utf-8"))
            # Migration: re-save as encrypted
            logger.info(f"[Memory] Migrating {path.name} to encrypted storage")
            self._save_json(path, data)
            return data
        except (json.JSONDecodeError, UnicodeDecodeError, Exception) as e:
            logger.error(f"[Memory] Failed to load {path.name}: {type(e).__name__}")
            return default

    def _save_json(self, path: Path, data):
        """Encrypt and save JSON data to file."""
        plaintext = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        fernet = _get_fernet()
        encrypted = fernet.encrypt(plaintext.encode("utf-8"))
        path.write_bytes(encrypted)

    # ── Memory (key=value Format) ─────────────────────────────────────────
    def save_memory_kv(self, kv_text: str):
        """Speichert key=value Text. z.B. 'name=Sebastian\\nstadt=Wien'"""
        with self._lock:
            for line in kv_text.strip().splitlines():
                line = line.strip()
                # Bug 1.6 fix: skip lines without "=" gracefully
                if not line or "=" not in line:
                    continue
                if len(line) > MAX_KV_LENGTH:
                    logger.warning("[Memory] The KV entry is too long (%d characters), skipped.", len(line))
                    continue
                key = line.split("=", 1)[0].strip().lower()
                # Remove old entries with the same key (guard against malformed kv)
                self.memories = [
                    m for m in self.memories
                    if not (
                        m.get("kv")
                        and "=" in m["kv"]
                        and m["kv"].split("=", 1)[0].strip().lower() == key
                    )
                ]
                entry = {
                    "id": len(self.memories) + int(datetime.now().timestamp()),
                    "timestamp": datetime.now().isoformat(),
                    "kv": line,
                    "aktiv": True,
                }
                self.memories.append(entry)
            self._save_json(self.memories_file, self.memories)

    def save_memory(self, trigger: str, content: str, category: str = "allgemein"):
        """Legacy – stores full text (compressed to kv internally where possible)."""
        entry = {
            "id": int(datetime.now().timestamp()),
            "timestamp": datetime.now().isoformat(),
            "trigger": trigger,
            "content": content,
            "category": category,
            "aktiv": True,
        }
        with self._lock:
            self.memories.append(entry)
            self._save_json(self.memories_file, self.memories)

    def get_relevant_context(self, query: str) -> str:
        active = [m for m in self.memories if m.get("aktiv", True)]
        if not active:
            return ""
        lines = []
        for m in active:
            if m.get("kv"):
                lines.append(m["kv"])
            elif m.get("content"):
                lines.append(m["content"][:80])
        return "\n".join(lines[:20]) if lines else ""

    def get_all_memories(self) -> list:
        return [m for m in self.memories if m.get("aktiv", True)]

    def delete_memory(self, memory_id):
        with self._lock:
            self.memories = [m for m in self.memories if m.get("id") != memory_id]
            self._save_json(self.memories_file, self.memories)

    # ── Routinen ──────────────────────────────────────────────────────────
    def add_routine(self, name, beschreibung, zeitplan):
        with self._lock:
            self.routines.append({
                "id": len(self.routines),
                "name": name,
                "beschreibung": beschreibung,
                "zeitplan": zeitplan,
                "erstellt": datetime.now().isoformat(),
                "aktiv": True,
            })
            self._save_json(self.routines_file, self.routines)

    def get_routines(self) -> list:
        return [r for r in self.routines if r.get("aktiv", True)]

    def delete_routine(self, rid):
        with self._lock:
            for r in self.routines:
                if r["id"] == rid:
                    r["aktiv"] = False
            self._save_json(self.routines_file, self.routines)

    # ── Projekte ──────────────────────────────────────────────────────────
    def add_project(self, name, beschreibung):
        with self._lock:
            self.projects.append({
                "id": len(self.projects),
                "name": name,
                "beschreibung": beschreibung,
                "erstellt": datetime.now().isoformat(),
                "status": "aktiv",
            })
            self._save_json(self.projects_file, self.projects)

    def get_active_projects(self) -> list:
        return [p for p in self.projects if p.get("status") == "aktiv"]

    # ── Historie ──────────────────────────────────────────────────────────
    def add_to_history(self, role: str, content: str):
        with self._lock:
            self.history.append({
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
            })
            if len(self.history) > MAX_HISTORY_ENTRIES:
                self.history = self.history[-MAX_HISTORY_ENTRIES:]
            self._save_json(self.history_file, self.history)

    def get_conversation_history(self, limit: int = 8) -> list:
        return self.history[-limit:] if self.history else []

    def clear_history(self):
        with self._lock:
            self.history = []
            self._save_json(self.history_file, self.history)

    def get_stats(self) -> dict:
        return {
            "memories": len(self.get_all_memories()),
            "routines": len(self.get_routines()),
            "projects": len(self.get_active_projects()),
            "history": len(self.history),
        }

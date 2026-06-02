"""
JARVIS Konfiguration v2.8 – Encrypted API Key Storage
"""
import json
import hashlib
import logging
import os
import platform
import getpass
import sys
from pathlib import Path

CONFIG_PATH = Path.home() / ".jarvis" / "config.json"
KEYRING_SENTINEL = "__KEYRING__"
FERNET_SENTINEL = "__FERNET__"
SERVICE_NAME = "jarvis-assistant"
ACCOUNT_NAME = "api_key"
SALT_PATH = Path.home() / ".jarvis" / ".config_salt"

# Bugs 10–13: weitere sensible Felder dürfen NICHT im Klartext in config.json
# landen. Sie werden inline mit Fernet (gleicher abgeleiteter Schlüssel)
# verschlüsselt und mit FERNET_PREFIX markiert. api_key bleibt separat
# (keyring-bevorzugt) – siehe _store_key/_retrieve_key.
SECRET_FIELDS = (
    "email_password",
    "notion_token",
    "todoist_token",
    "ha_token",
)
FERNET_PREFIX = "__FERNET__:"   # Markierung für inline-verschlüsselte Werte

logger = logging.getLogger("jarvis.config")

DEFAULT_CONFIG = {
    "name": "JARVIS",
    "anrede": "Sir",
    "sprache": "de-DE",
    "redeart": "professionell",
    "api_provider": "anthropic",
    "api_key": "",
    "webcam_erlaubt": False,
    "wohnort": "Wien, Österreich",
    "aktien_symbole": ["AAPL", "MSFT", "NVDA"],
    "tts_enabled": True,
    "tts_voice": "",
    "suggestions_enabled": True,
}


def _get_machine_secret() -> bytes:
    """Derive a machine-specific secret from hostname + username."""
    hostname = platform.node()
    username = getpass.getuser()
    return f"{hostname}:{username}:jarvis-v2".encode("utf-8")


def _get_or_create_salt() -> bytes:
    """Get or create a persistent salt for Fernet key derivation."""
    SALT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SALT_PATH.exists():
        return SALT_PATH.read_bytes()
    salt = os.urandom(16)
    SALT_PATH.write_bytes(salt)
    return salt


def _derive_fernet_key() -> bytes:
    """Derive a Fernet key from the machine secret + salt via PBKDF2."""
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    import base64

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


def _store_key(api_key: str) -> str:
    """
    Store the API key securely. Returns the sentinel to write into config.json.
    Tries keyring (Windows DPAPI / WinVault) first, falls back to Fernet encryption.
    """
    if not api_key or api_key in (KEYRING_SENTINEL, FERNET_SENTINEL):
        return api_key

    # Try keyring first
    try:
        import keyring
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, api_key)
        # Verify it was stored
        verify = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
        if verify == api_key:
            logger.debug("[Config] API key stored in system keyring")
            return KEYRING_SENTINEL
    except Exception as e:
        logger.debug(f"[Config] Keyring unavailable, using Fernet fallback: {type(e).__name__}")

    # Fallback: Fernet encryption stored in a sidecar file
    try:
        from cryptography.fernet import Fernet

        fernet_key = _derive_fernet_key()
        f = Fernet(fernet_key)
        encrypted = f.encrypt(api_key.encode("utf-8"))
        sidecar = CONFIG_PATH.parent / ".api_key.enc"
        sidecar.write_bytes(encrypted)
        logger.debug("[Config] API key stored via Fernet encryption")
        return FERNET_SENTINEL
    except Exception as e:
        logger.error(f"[Config] Secure storage failed completely: {type(e).__name__}")
        raise RuntimeError(
            "API-Key konnte nicht sicher gespeichert werden. "
            "Bitte 'cryptography' installieren: pip install cryptography"
        )


def _retrieve_key(sentinel: str) -> str:
    """
    Retrieve the API key from secure storage based on the sentinel value.
    """
    if sentinel == KEYRING_SENTINEL:
        try:
            import keyring
            key = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
            if key:
                return key
        except Exception as e:
            logger.debug(f"[Config] Keyring retrieval failed: {type(e).__name__}")

        # If keyring fails, try Fernet sidecar as fallback
        sentinel = FERNET_SENTINEL

    if sentinel == FERNET_SENTINEL:
        try:
            from cryptography.fernet import Fernet

            sidecar = CONFIG_PATH.parent / ".api_key.enc"
            if sidecar.exists():
                fernet_key = _derive_fernet_key()
                f = Fernet(fernet_key)
                encrypted = sidecar.read_bytes()
                return f.decrypt(encrypted).decode("utf-8")
        except Exception as e:
            logger.warning(f"[Config] Fernet retrieval failed: {type(e).__name__}")

    # If it's neither sentinel, it might be a plaintext key (pre-v2.0 config)
    if sentinel and sentinel not in (KEYRING_SENTINEL, FERNET_SENTINEL):
        return sentinel

    return ""


def _encrypt_value(value: str) -> str:
    """Verschlüsselt einen einzelnen Secret-Wert inline (Fernet).

    Gibt FERNET_PREFIX + Token zurück. Leere/bereits verschlüsselte Werte
    werden unverändert durchgereicht. Schlägt die Verschlüsselung fehl
    (z.B. fehlendes 'cryptography'), wird eine RuntimeError geworfen statt
    still Klartext zu speichern.
    """
    if not value or not isinstance(value, str) or value.startswith(FERNET_PREFIX):
        return value
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_fernet_key())
        token = f.encrypt(value.encode("utf-8")).decode("ascii")
        return FERNET_PREFIX + token
    except Exception as e:
        logger.error("[Config] Secret-Verschlüsselung fehlgeschlagen: %s", type(e).__name__)
        raise RuntimeError(
            "Sensibles Feld konnte nicht verschlüsselt werden. "
            "Bitte 'cryptography' installieren: pip install cryptography"
        )


def _decrypt_value(value: str) -> str:
    """Entschlüsselt einen inline-verschlüsselten Secret-Wert wieder zu Klartext."""
    if not value or not isinstance(value, str) or not value.startswith(FERNET_PREFIX):
        return value
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_fernet_key())
        return f.decrypt(value[len(FERNET_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception as e:
        logger.warning("[Config] Secret-Entschlüsselung fehlgeschlagen: %s", type(e).__name__)
        return ""


class Config:
    @staticmethod
    def exists() -> bool:
        return CONFIG_PATH.exists()

    @staticmethod
    def load() -> dict:
        base = DEFAULT_CONFIG.copy()
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                base.update(saved)
            except Exception as e:
                logger.error(f"[Config] Ladefehler: {type(e).__name__}")

        # Retrieve the real API key from secure storage
        needs_migration = False
        raw_key = base.get("api_key", "")
        if raw_key in (KEYRING_SENTINEL, FERNET_SENTINEL):
            base["api_key"] = _retrieve_key(raw_key)
        elif raw_key:
            # Legacy plaintext key found — migrate immediately to secure storage
            logger.warning("[Config] Klartext-API-Key gefunden – migriere zu verschlüsselter Speicherung.")
            needs_migration = True

        # Bugs 10–13: sensible Felder entschlüsseln; Klartext-Altbestände migrieren
        for field in SECRET_FIELDS:
            val = base.get(field, "")
            if isinstance(val, str) and val.startswith(FERNET_PREFIX):
                base[field] = _decrypt_value(val)
            elif val:
                logger.warning("[Config] Klartext-Secret '%s' gefunden – migriere zu "
                               "verschlüsselter Speicherung.", field)
                needs_migration = True

        # Eine einzige Migrations-Speicherung verschlüsselt api_key + alle Secrets
        if needs_migration:
            try:
                Config.save(base)
                logger.info("[Config] Migration zu verschlüsselter Speicherung abgeschlossen.")
            except Exception as e:
                logger.error("[Config] Migration fehlgeschlagen: %s", e)

        return base

    @staticmethod
    def save(config: dict):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Work on a copy so we don't mutate the caller's dict
        to_save = config.copy()

        # Remove internal migration flag if present
        migrate = to_save.pop("_migrate_api_key", False)

        # Store API key securely
        api_key = to_save.get("api_key", "")
        if api_key and api_key not in (KEYRING_SENTINEL, FERNET_SENTINEL):
            sentinel = _store_key(api_key)
            to_save["api_key"] = sentinel
        elif not api_key:
            to_save["api_key"] = ""

        # Bugs 10–13: sensible Felder verschlüsselt ablegen (nie Klartext auf Disk)
        for field in SECRET_FIELDS:
            val = to_save.get(field, "")
            if val and isinstance(val, str) and not val.startswith(FERNET_PREFIX):
                to_save[field] = _encrypt_value(val)

        # Remove any legacy/non-standard plaintext API key fields
        _LEGACY_KEY_FIELDS = ("anthropic_api_key", "openai_api_key", "gemini_api_key")
        for field in _LEGACY_KEY_FIELDS:
            if field in to_save:
                logger.warning("[Config] Entferne Legacy-Klartextfeld '%s'.", field)
                to_save.pop(field)

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2, ensure_ascii=False)

        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass

    @staticmethod
    def first_setup():
        # Guard: nur im interaktiven Terminal ausführen, sonst Default speichern
        if not (sys.stdin and sys.stdin.isatty()):
            logger.warning(
                "[Config] first_setup() ohne TTY aufgerufen – speichere Defaults. "
                "Konfiguration anschließend über GUI vornehmen."
            )
            config = DEFAULT_CONFIG.copy()
            Config.save(config)
            return config

        print("\n╔══════════════════════════════════╗")
        print("║   JARVIS ERSTKONFIGURATION v2.8  ║")
        print("╚══════════════════════════════════╝\n")

        config = DEFAULT_CONFIG.copy()

        print("Wie soll JARVIS Sie ansprechen?")
        print("  1) Sir   2) Ma'am   3) Bro   4) Chef   5) Kein Titel")
        c = input("Wahl (1-5): ").strip()
        config["anrede"] = {"1": "Sir", "2": "Ma'am", "3": "Bro", "4": "Chef", "5": ""}.get(c, "Sir")

        print("\nRedeart?")
        print("  1) Professionell   2) Normal   3) Jugendlich")
        r = input("Wahl (1-3): ").strip()
        config["redeart"] = {"1": "professionell", "2": "normal", "3": "jugendlich"}.get(r, "normal")

        print("\nKI-Anbieter?")
        print("  1) Anthropic Claude   2) OpenAI ChatGPT   3) Google Gemini")
        print("  4) NVIDIA NIM         5) Mistral          6) Lokal (Ollama)")
        p = input("Wahl (1-6): ").strip()
        providers = {"1": "anthropic", "2": "openai", "3": "gemini", "4": "nvidia", "5": "mistral", "6": "local"}
        config["api_provider"] = providers.get(p, "anthropic")

        if config["api_provider"] != "local":
            config["api_key"] = input(f"\nAPI-Key für {config['api_provider']}: ").strip()
        else:
            config["api_key"] = ""
            config["local_url"] = input("Ollama URL (z.B. http://localhost:11434): ").strip() or "http://localhost:11434"
            config["local_model"] = input("Modell (z.B. llama3): ").strip() or "llama3"

        ort = input("\nWohnort für Wetter (z.B. Wien, Österreich): ").strip()
        if ort:
            config["wohnort"] = ort

        Config.save(config)
        print("\n✅ Konfiguration gespeichert! (API-Key verschlüsselt)\n")
        return config

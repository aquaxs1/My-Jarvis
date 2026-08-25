"""
My Jarvis configuration v2.8 – encrypted API key storage
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

# Bugs 10-13: further sensitive fields must NOT land in config.json as plain
# text. They are encrypted inline with Fernet (the same derived key) and marked
# with FERNET_PREFIX. api_key stays separate (keyring preferred) – see
# _store_key/_retrieve_key.
SECRET_FIELDS = (
    "email_password",
    "notion_token",
    "todoist_token",
    "ha_token",
)
FERNET_PREFIX = "__FERNET__:"   # marks inline-encrypted values

logger = logging.getLogger("jarvis.config")

# v3.0: config keys used to be German. Old config.json files are migrated on
# load, so an existing installation keeps its settings.
LEGACY_KEYS = {
    "anrede": "salutation",
    "sprache": "language",
    "redeart": "tone",
    "webcam_erlaubt": "webcam_allowed",
    "wohnort": "location",
    "aktien_symbole": "stock_symbols",
}
LEGACY_TONES = {"professionell": "professional", "jugendlich": "casual"}

DEFAULT_CONFIG = {
    "name": "My Jarvis",
    "salutation": "Sir",
    "language": "en-US",
    "tone": "professional",
    "api_provider": "anthropic",
    "api_key": "",
    "webcam_allowed": False,
    "location": "Vienna, Austria",
    "stock_symbols": ["AAPL", "MSFT", "NVDA"],
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
            "The API key could not be stored securely. "
            "Please install 'cryptography': pip install cryptography"
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
    """Encrypts a single secret value inline (Fernet).

    Returns FERNET_PREFIX + token. Empty or already encrypted values are passed
    through unchanged. If encryption fails (e.g. 'cryptography' is missing) a
    RuntimeError is raised instead of quietly storing plain text.
    """
    if not value or not isinstance(value, str) or value.startswith(FERNET_PREFIX):
        return value
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_fernet_key())
        token = f.encrypt(value.encode("utf-8")).decode("ascii")
        return FERNET_PREFIX + token
    except Exception as e:
        logger.error("[Config] Secret encryption failed: %s", type(e).__name__)
        raise RuntimeError(
            "A sensitive field could not be encrypted. "
            "Please install 'cryptography': pip install cryptography"
        )


def _decrypt_value(value: str) -> str:
    """Decrypts an inline-encrypted secret value back to plain text."""
    if not value or not isinstance(value, str) or not value.startswith(FERNET_PREFIX):
        return value
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_fernet_key())
        return f.decrypt(value[len(FERNET_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception as e:
        logger.warning("[Config] Secret decryption failed: %s", type(e).__name__)
        return ""


def _migrate_legacy_keys(saved: dict) -> dict:
    """v3.0: rename the old German config keys and values to their English names.

    Runs on every load, so a config.json written by an earlier version keeps
    working. New keys always win when both are present.
    """
    if not isinstance(saved, dict):
        return {}
    out = dict(saved)
    for old, new in LEGACY_KEYS.items():
        if old in out:
            value = out.pop(old)
            out.setdefault(new, value)
    tone = out.get("tone")
    if isinstance(tone, str) and tone in LEGACY_TONES:
        out["tone"] = LEGACY_TONES[tone]
    return out


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
                base.update(_migrate_legacy_keys(saved))
            except Exception as e:
                logger.error(f"[Config] Load error: {type(e).__name__}")

        # Retrieve the real API key from secure storage
        needs_migration = False
        raw_key = base.get("api_key", "")
        if raw_key in (KEYRING_SENTINEL, FERNET_SENTINEL):
            base["api_key"] = _retrieve_key(raw_key)
        elif raw_key:
            # Legacy plaintext key found — migrate immediately to secure storage
            logger.warning("[Config] Plain-text API key found – migrating to encrypted storage.")
            needs_migration = True

        # Bugs 10-13: decrypt sensitive fields; migrate any plain-text leftovers
        for field in SECRET_FIELDS:
            val = base.get(field, "")
            if isinstance(val, str) and val.startswith(FERNET_PREFIX):
                base[field] = _decrypt_value(val)
            elif val:
                logger.warning("[Config] Plain-text secret '%s' found – migrating to "
                               "encrypted storage.", field)
                needs_migration = True

        # a single migration save encrypts api_key + every secret
        if needs_migration:
            try:
                Config.save(base)
                logger.info("[Config] Migration to encrypted storage complete.")
            except Exception as e:
                logger.error("[Config] Migration failed: %s", e)

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

        # Bugs 10-13: store sensitive fields encrypted (never plain text on disk)
        for field in SECRET_FIELDS:
            val = to_save.get(field, "")
            if val and isinstance(val, str) and not val.startswith(FERNET_PREFIX):
                to_save[field] = _encrypt_value(val)

        # Remove any legacy/non-standard plaintext API key fields
        _LEGACY_KEY_FIELDS = ("anthropic_api_key", "openai_api_key", "gemini_api_key")
        for field in _LEGACY_KEY_FIELDS:
            if field in to_save:
                logger.warning("[Config] Removing legacy plain-text field '%s'.", field)
                to_save.pop(field)

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2, ensure_ascii=False)

        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass

    @staticmethod
    def first_setup():
        # guard: only run in an interactive terminal, otherwise save the defaults
        if not (sys.stdin and sys.stdin.isatty()):
            logger.warning(
                "[Config] first_setup() called without a TTY – saving the defaults. "
                "Configure it afterwards through the GUI."
            )
            config = DEFAULT_CONFIG.copy()
            Config.save(config)
            return config

        print("\n╔══════════════════════════════════╗")
        print("║    MY JARVIS FIRST SETUP v2.8    ║")
        print("╚══════════════════════════════════╝\n")

        config = DEFAULT_CONFIG.copy()

        print("How should My Jarvis address you?")
        print("  1) Sir   2) Ma'am   3) Bro   4) Boss   5) No title")
        c = input("Choice (1-5): ").strip()
        config["salutation"] = {"1": "Sir", "2": "Ma'am", "3": "Bro", "4": "Boss", "5": ""}.get(c, "Sir")

        print("\nTone?")
        print("  1) Professional   2) Normal   3) Casual")
        r = input("Choice (1-3): ").strip()
        config["tone"] = {"1": "professional", "2": "normal", "3": "casual"}.get(r, "normal")

        print("\nAI provider?")
        print("  1) Anthropic Claude   2) OpenAI ChatGPT   3) Google Gemini")
        print("  4) NVIDIA NIM         5) Mistral          6) Local (Ollama)")
        p = input("Choice (1-6): ").strip()
        providers = {"1": "anthropic", "2": "openai", "3": "gemini", "4": "nvidia", "5": "mistral", "6": "local"}
        config["api_provider"] = providers.get(p, "anthropic")

        if config["api_provider"] != "local":
            config["api_key"] = input(f"\nAPI key for {config['api_provider']}: ").strip()
        else:
            config["api_key"] = ""
            config["local_url"] = input("Ollama URL (e.g. http://localhost:11434): ").strip() or "http://localhost:11434"
            config["local_model"] = input("Model (e.g. llama3): ").strip() or "llama3"

        place = input("\nLocation for the weather (e.g. Vienna, Austria): ").strip()
        if place:
            config["location"] = place

        Config.save(config)
        print("\n✅ Configuration saved! (the API key is encrypted)\n")
        return config

"""
JARVIS Code Processor
Tests and formats code blocks in API responses before display.
"""
import os
import re
import sys
import shutil
import subprocess
import tempfile
import logging

logger = logging.getLogger(__name__)

_CODE_BLOCK_RE = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)
_LANG_ALIASES = {"py": "python", "js": "javascript"}


def extract_code_blocks(text: str) -> list:
    """Extract all fenced code blocks from markdown text."""
    blocks = []
    for match in _CODE_BLOCK_RE.finditer(text):
        blocks.append({
            "language": match.group(1).lower().strip(),
            "code": match.group(2),
            "start": match.start(),
            "end": match.end(),
        })
    return blocks


def _normalize_lang(lang: str) -> str:
    return _LANG_ALIASES.get(lang, lang)


def test_code(language: str, code: str) -> tuple:
    """Test code syntax. Returns (success, error_message)."""
    lang = _normalize_lang(language)
    if lang == "python":
        return _test_python(code)
    elif lang == "javascript":
        return _test_javascript(code)
    return True, ""


def _test_python(code: str) -> tuple:
    fd, path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            return False, error
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Syntax-Check Timeout"
    except FileNotFoundError as e:
        logger.error("[CodeProcessor] The Python interpreter is not available: %s", e)
        return False, f"The Python interpreter is not available: {e}"
    except OSError as e:
        logger.error("[CodeProcessor] OS error during the Python check: %s", e)
        return False, f"Python-Syntax-Check fehlgeschlagen: {e}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _test_javascript(code: str) -> tuple:
    if not shutil.which("node"):
        logger.info("[CodeProcessor] node is not installed – the JS check was skipped.")
        return True, ""
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        result = subprocess.run(
            ["node", "--check", path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            return False, error
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Syntax-Check Timeout"
    except FileNotFoundError as e:
        logger.error("[CodeProcessor] node nicht aufrufbar: %s", e)
        return False, f"node is not available: {e}"
    except OSError as e:
        logger.error("[CodeProcessor] OS error during the JS check: %s", e)
        return False, f"JS-Syntax-Check fehlgeschlagen: {e}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def format_code(language: str, code: str) -> str:
    """Format code with available formatters. Returns original if none available."""
    lang = _normalize_lang(language)
    if lang == "python":
        return _format_python(code)
    elif lang in ("javascript", "html", "css"):
        return _format_prettier(code, language)
    return code


def _format_python(code: str) -> str:
    for tool, args in [("black", ["black", "--quiet", "-"]),
                       ("autopep8", ["autopep8", "-"])]:
        if not shutil.which(tool):
            continue
        try:
            result = subprocess.run(
                args, input=code, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
            if result.stderr:
                logger.warning("[CodeProcessor] %s liefert Fehler: %s", tool, result.stderr.strip())
        except subprocess.TimeoutExpired:
            logger.warning("[CodeProcessor] %s Timeout – Original-Code beibehalten.", tool)
        except (OSError, FileNotFoundError) as e:
            logger.warning("[CodeProcessor] %s nicht aufrufbar: %s", tool, e)
    return code


def _format_prettier(code: str, language: str) -> str:
    if not shutil.which("prettier"):
        return code
    ext_map = {
        "javascript": "file.js", "js": "file.js",
        "html": "file.html", "css": "file.css",
    }
    filepath = ext_map.get(language.lower(), "file.js")
    try:
        result = subprocess.run(
            ["prettier", "--stdin-filepath", filepath],
            input=code, capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        if result.stderr:
            logger.warning("[CodeProcessor] prettier liefert Fehler: %s", result.stderr.strip())
    except subprocess.TimeoutExpired:
        logger.warning("[CodeProcessor] prettier Timeout – Original-Code beibehalten.")
    except (OSError, FileNotFoundError) as e:
        logger.warning("[CodeProcessor] prettier nicht aufrufbar: %s", e)
    return code

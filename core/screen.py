"""
JARVIS Bildschirm-Beobachter
Sieht was auf dem Bildschirm passiert
"""

import base64
import platform
from typing import Optional

# v3.0 Speed: Vision-Screenshots als JPEG statt PNG kodieren.
# JPEG ist deutlich kleiner und schneller zu (de)kodieren/übertragen — bei
# 1280px Breite typisch ~5-8x kleiner als PNG. Bei Quality 80 bleibt UI-Text
# gut lesbar. Das beschleunigt vor allem den Copilot-Loop (viele Screenshots
# pro Aufgabe) und die Bildschirm-Analyse spürbar. Die Modell-Media-Types in
# core/brain.py (vision_decide / analyze_screenshot) sind passend auf JPEG
# gesetzt – beides MUSS konsistent bleiben.
VISION_MEDIA_TYPE = "image/jpeg"
VISION_JPEG_QUALITY = 80


class ScreenWatcher:
    def __init__(self):
        self.os = platform.system()
        self._init()

    def _init(self):
        try:
            import pyautogui
            from PIL import Image
            self.available = True
            self.pag = pyautogui
        except ImportError:
            self.available = False
            print("[Screen] PyAutoGUI/PIL nicht verfügbar")

    def take_screenshot(self, max_width: int = 2560) -> Optional[str]:
        """Screenshot als Base64, optional verkleinert."""
        if not self.available:
            return None
        try:
            screenshot = self.pag.screenshot()
            w, h = screenshot.size
            if w > max_width:
                ratio = max_width / w
                screenshot = screenshot.resize((max_width, int(h * ratio)))
            if screenshot.mode != "RGB":          # JPEG kennt keinen Alphakanal
                screenshot = screenshot.convert("RGB")
            import io
            buffer = io.BytesIO()
            screenshot.save(buffer, format="JPEG", quality=VISION_JPEG_QUALITY)
            return base64.b64encode(buffer.getvalue()).decode()
        except Exception as e:
            print(f"[Screen] Screenshot-Fehler: {e}")
            return None

    def get_description(self) -> str:
        """Kurzbeschreibung des aktuellen Bildschirminhalts"""
        if not self.available:
            return ""
        try:
            # Aktives Fenster ermitteln
            if self.os == "Windows":
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                return f"Aktives Fenster: {buf.value}"
            elif self.os == "Darwin":
                import subprocess
                result = subprocess.run(
                    ["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
                    capture_output=True, text=True
                )
                return f"Aktive App: {result.stdout.strip()}"
        except (OSError, ImportError, subprocess.SubprocessError) as e:
            print(f"[Screen] Fenstererkennung fehlgeschlagen: {e}")
        except Exception as e:
            print(f"[Screen] Unerwarteter Fehler bei Fenstererkennung: {e}")
        return ""

    def get_screen_size(self) -> tuple:
        if self.available:
            return self.pag.size()
        return (1920, 1080)

    def capture_for_vision(self, target_width: int = 1280):
        """Screenshot für den Copilot.

        Skaliert den Screenshot auf 'target_width' (Seitenverhältnis bleibt
        erhalten) und liefert die Metadaten, die nötig sind um vom Bild-Raum
        zurück in den echten Klick-Raum zu rechnen.

        Returns:
            (b64_png, vision_w, vision_h, real_w, real_h) oder None.

        - vision_w/vision_h: Maße des Bildes, das das Modell sieht.
        - real_w/real_h:     Klick-Koordinatenraum von PyAutoGUI (size()),
                             unabhängig von der physischen Pixelauflösung.
        """
        if not self.available:
            return None
        try:
            import io
            import base64 as _b64
            screenshot = self.pag.screenshot()
            orig_w, orig_h = screenshot.size
            if orig_w > target_width:
                ratio = target_width / orig_w
                vision_w = target_width
                vision_h = max(1, int(orig_h * ratio))
                screenshot = screenshot.resize((vision_w, vision_h))
            else:
                vision_w, vision_h = orig_w, orig_h

            if screenshot.mode != "RGB":          # JPEG kennt keinen Alphakanal
                screenshot = screenshot.convert("RGB")
            buffer = io.BytesIO()
            screenshot.save(buffer, format="JPEG", quality=VISION_JPEG_QUALITY)
            b64 = _b64.b64encode(buffer.getvalue()).decode()

            try:
                real_w, real_h = self.pag.size()
            except Exception:
                real_w, real_h = orig_w, orig_h

            return (b64, vision_w, vision_h, int(real_w), int(real_h))
        except Exception as e:
            print(f"[Screen] capture_for_vision Fehler: {e}")
            return None

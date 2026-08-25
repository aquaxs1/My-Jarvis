"""
My Jarvis wake word & clap detection
- wake word: "Hey Jarvis" via speech_recognition (fallback) or pvporcupine
- clap detection: two claps, via amplitude analysis
- one shared audio thread, to avoid microphone conflicts
"""
import threading
import time
import logging
import struct
from typing import Optional, Callable

logger = logging.getLogger(__name__)

WAKE_PHRASES = ["hey jarvis", "jarvis", "hey dscharwis", "hey tscharwis", "hey djarvis"]
CLAP_THRESHOLD_DEFAULT = 0.3
CLAP_MIN_GAP = 0.1
CLAP_MAX_GAP = 1.0
CLAP_MAX_DURATION = 0.2
CHUNK_DURATION = 0.05
SAMPLE_RATE = 16000
CHANNELS = 1


class WakeWordListener:
    def __init__(self, config: dict, on_trigger: Callable):
        self.config = config
        self.on_trigger = on_trigger
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._porcupine = None
        self._use_porcupine = False
        self._ambient_rms = 0.0

    @property
    def wake_word_enabled(self) -> bool:
        return self.config.get("wake_word_enabled", False)

    @property
    def clap_enabled(self) -> bool:
        return self.config.get("clap_enabled", False)

    @property
    def clap_threshold(self) -> float:
        return self.config.get("clap_threshold", CLAP_THRESHOLD_DEFAULT)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running():
            return
        if not self.wake_word_enabled and not self.clap_enabled:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("[WakeWord] listener started (wake=%s, clap=%s)",
                    self.wake_word_enabled, self.clap_enabled)

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        if self._porcupine:
            try:
                self._porcupine.delete()
            except Exception:
                pass
            self._porcupine = None
        logger.info("[WakeWord] listener stopped")

    def _run(self):
        try:
            self._try_porcupine()
        except Exception as e:
            logger.info("[WakeWord] Porcupine is not available: %s", e)
            self._use_porcupine = False

        if self._use_porcupine:
            self._run_porcupine_loop()
        else:
            self._run_fallback_loop()

    def _try_porcupine(self):
        try:
            import pvporcupine
            access_key = self.config.get("porcupine_access_key", "")
            if not access_key:
                raise ValueError("Kein Porcupine Access Key")
            self._porcupine = pvporcupine.create(
                access_key=access_key,
                keywords=["jarvis"]
            )
            self._use_porcupine = True
            logger.info("[WakeWord] Porcupine initialisiert")
        except (ImportError, ValueError, Exception) as e:
            self._use_porcupine = False
            raise

    def _run_porcupine_loop(self):
        try:
            import pyaudio
        except ImportError:
            logger.warning("[WakeWord] pyaudio is missing for Porcupine mode")
            self._use_porcupine = False
            self._run_fallback_loop()
            return

        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(
                rate=self._porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self._porcupine.frame_length
            )
            logger.info("[WakeWord] Porcupine lauscht...")
            while not self._stop_event.is_set():
                pcm = stream.read(self._porcupine.frame_length, exception_on_overflow=False)
                pcm_unpacked = struct.unpack_from("h" * self._porcupine.frame_length, pcm)

                if self.wake_word_enabled:
                    keyword_index = self._porcupine.process(pcm_unpacked)
                    if keyword_index >= 0:
                        logger.info("[WakeWord] 'Jarvis' erkannt (Porcupine)")
                        self._trigger()

                if self.clap_enabled:
                    self._check_clap_from_pcm(pcm_unpacked)

        except Exception as e:
            logger.error("[WakeWord] Porcupine loop error: %s", e)
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            pa.terminate()

    def _run_fallback_loop(self):
        try:
            import speech_recognition as sr
        except ImportError:
            logger.error("[WakeWord] speech_recognition is not installed")
            return

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.5

        logger.info("[WakeWord] Fallback mode (speech_recognition) active")

        while not self._stop_event.is_set():
            if not self.wake_word_enabled and not self.clap_enabled:
                self._stop_event.wait(1)
                continue

            if self.clap_enabled:
                self._run_clap_check_cycle()

            if not self.wake_word_enabled:
                self._stop_event.wait(0.5)
                continue

            try:
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.2)
                    audio = recognizer.listen(source, timeout=3, phrase_time_limit=3)

                if self._stop_event.is_set():
                    break

                try:
                    text = recognizer.recognize_google(
                        audio, language=self.config.get("language", "de-DE")
                    ).lower()
                    if any(phrase in text for phrase in WAKE_PHRASES):
                        logger.info("[WakeWord] '%s' erkannt (Fallback)", text)
                        self._trigger()
                except sr.UnknownValueError:
                    pass
                except sr.RequestError as e:
                    logger.warning("[WakeWord] Google STT error: %s", e)
                    self._stop_event.wait(5)

            except sr.WaitTimeoutError:
                pass
            except Exception as e:
                logger.debug("[WakeWord] Listen error: %s", e)
                self._stop_event.wait(1)

    # ── Clap Detection ───────────────────────────────────────────────────
    _last_clap_time = 0.0
    _clap_count = 0

    def _run_clap_check_cycle(self):
        try:
            import sounddevice as sd
            import numpy as np
        except ImportError:
            return

        chunk_samples = int(SAMPLE_RATE * CHUNK_DURATION)
        try:
            audio = sd.rec(chunk_samples, samplerate=SAMPLE_RATE,
                          channels=CHANNELS, dtype='float32', blocking=True)
            rms = float(np.sqrt(np.mean(audio ** 2)))
            self._process_clap_rms(rms)
        except Exception:
            pass

    def _check_clap_from_pcm(self, pcm_data):
        import math
        n = len(pcm_data)
        if n == 0:
            return
        sum_sq = sum(s * s for s in pcm_data)
        rms = math.sqrt(sum_sq / n) / 32768.0
        self._process_clap_rms(rms)

    def _process_clap_rms(self, rms: float):
        now = time.monotonic()
        threshold = self.clap_threshold

        if rms > threshold:
            gap = now - self._last_clap_time
            if gap > CLAP_MIN_GAP:
                if self._clap_count == 0 or gap <= CLAP_MAX_GAP:
                    self._clap_count += 1
                    self._last_clap_time = now
                else:
                    self._clap_count = 1
                    self._last_clap_time = now

                if self._clap_count >= 2:
                    logger.info("[WakeWord] 2x Klatschen erkannt (RMS=%.3f)", rms)
                    self._clap_count = 0
                    self._trigger()
        else:
            if self._clap_count > 0 and (now - self._last_clap_time) > CLAP_MAX_GAP:
                self._clap_count = 0

    def _trigger(self):
        try:
            self.on_trigger()
        except Exception as e:
            logger.error("[WakeWord] Trigger callback error: %s", e)
        time.sleep(2)

    def calibrate_clap(self, duration: float = 3.0) -> float:
        try:
            import sounddevice as sd
            import numpy as np
        except ImportError:
            return CLAP_THRESHOLD_DEFAULT

        logger.info("[WakeWord] Calibrating the background noise (%ss)...", duration)
        try:
            samples = int(SAMPLE_RATE * duration)
            audio = sd.rec(samples, samplerate=SAMPLE_RATE,
                          channels=CHANNELS, dtype='float32', blocking=True)
            rms = float(np.sqrt(np.mean(audio ** 2)))
            threshold = max(rms * 3.0, 0.05)
            threshold = min(threshold, 0.9)
            logger.info("[WakeWord] Ambient RMS=%.4f → Threshold=%.3f", rms, threshold)
            return threshold
        except Exception as e:
            logger.error("[WakeWord] calibration failed: %s", e)
            return CLAP_THRESHOLD_DEFAULT

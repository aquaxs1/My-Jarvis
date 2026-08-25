"""
My Jarvis speech engine v2.8
- TTS: sentence chunking instead of a hard 500-character limit
- multilingual: en-US, de-DE, es-ES, fr-FR, it-IT, ja-JP
- TTS on/off through the config
- stop() aborts the recording
"""
import threading
import queue
import time
import re
from typing import Optional

# ── Konfigurationskonstanten ──────────────────────────────────────────────
SPEECH_RATE = 170
SPEECH_VOLUME = 0.9
STT_ENERGY_THRESHOLD = 300
STT_PAUSE_THRESHOLD = 0.8
STT_LISTEN_TIMEOUT = 10
STT_PHRASE_LIMIT = 20


class SpeechEngine:
    def __init__(self, config: dict):
        self.config       = config
        self.tts_queue    = queue.Queue()
        self.is_speaking  = False
        self._stop_flag   = threading.Event()
        self._init_tts()
        self._init_stt()
        self._tts_thread  = threading.Thread(target=self._tts_worker, daemon=True)
        self._tts_thread.start()

    def get_lang(self):
        return self.config.get("language", "de-DE")

    def tts_enabled(self):
        return self.config.get("tts_enabled", True)

    # ── TTS Init ──────────────────────────────────────────────────────────
    def _init_tts(self):
        try:
            import pyttsx3
            self.tts_engine = pyttsx3.init()
            self._set_voice(self.get_lang())
            self.tts_engine.setProperty('rate', SPEECH_RATE)
            self.tts_engine.setProperty('volume', SPEECH_VOLUME)
            self.tts_backend = "pyttsx3"
            print("[TTS] pyttsx3 initialisiert")
        except Exception:
            self.tts_backend = "none"
            print("[TTS] No TTS backend – the text is only displayed.")

    def _set_voice(self, lang_code: str):
        """Matches the voice to the language."""
        if self.tts_backend != "pyttsx3":
            return
        # maps lang_code → keywords used to find a voice
        lang_keywords = {
            "de-DE": ["german","deutsch","de_","helena","hedda"],
            "en-US": ["english","en_","zira","david","samantha"],
            "es-ES": ["spanish","español","es_","helena"],
            "fr-FR": ["french","français","fr_","thomas","virginie"],
            "it-IT": ["italian","italiano","it_"],
            "ja-JP": ["japanese","日本語","ja_"],
        }
        keywords = lang_keywords.get(lang_code, ["german","deutsch"])
        voices = self.tts_engine.getProperty('voices')
        for v in voices:
            if any(k.lower() in v.name.lower() or k.lower() in v.id.lower() for k in keywords):
                self.tts_engine.setProperty('voice', v.id)
                print(f"[TTS] Stimme: {v.name}")
                return

    def update_language(self, lang_code: str):
        self.config["language"] = lang_code
        if self.tts_backend == "pyttsx3":
            try:
                import pyttsx3
                self.tts_engine = pyttsx3.init()
                self._set_voice(lang_code)
                self.tts_engine.setProperty('rate', SPEECH_RATE)
                self.tts_engine.setProperty('volume', SPEECH_VOLUME)
            except Exception as e:
                print(f"[TTS] re-init failed: {e}")

    # ── Sprechen ──────────────────────────────────────────────────────────
    def speak(self, text: str, priority: bool = False):
        if not self.tts_enabled():
            return
        if priority:
            while not self.tts_queue.empty():
                try:
                    self.tts_queue.get_nowait()
                except queue.Empty:
                    break
        self.tts_queue.put(text)

    def _tts_worker(self):
        while True:
            text = self.tts_queue.get()
            self.is_speaking = True
            self._speak_chunked(text)
            self.is_speaking = False
            self.tts_queue.task_done()

    def _speak_chunked(self, text: str):
        """Splits long text into sentences – no more hard truncation."""
        self._stop_flag.clear()
        # strip code blocks and markdown
        text = re.sub(r'```[\s\S]*?```', '[Code-Block]', text)
        text = re.sub(r'`[^`]+`', '', text)
        text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
        text = re.sub(r'#+\s', '', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

        # split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())

        for sentence in sentences:
            if self._stop_flag.is_set():
                break
            sentence = sentence.strip()
            if not sentence or len(sentence) < 2:
                continue
            self._speak_sentence(sentence)

    def _speak_sentence(self, text: str):
        if self.tts_backend == "pyttsx3":
            try:
                self.tts_engine.say(text)
                self.tts_engine.runAndWait()
            except Exception as e:
                print(f"[TTS] Error: {e}")
        else:
            print(f"[JARVIS] {text}")

    def stop_speaking(self):
        """Unterbricht laufende Sprachausgabe."""
        self._stop_flag.set()
        if self.tts_backend == "pyttsx3":
            try:
                self.tts_engine.stop()
            except Exception:
                pass

    # ── STT Init ──────────────────────────────────────────────────────────
    def _init_stt(self):
        self.stt_backend   = "none"
        self.stt_available = False
        try:
            import speech_recognition as sr
            self.recognizer = sr.Recognizer()
            self.recognizer.energy_threshold      = STT_ENERGY_THRESHOLD
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.pause_threshold       = STT_PAUSE_THRESHOLD
            try:
                import pyaudio; pyaudio.PyAudio()
                self.stt_backend = "pyaudio"; self.stt_available = True
                print("[STT] PyAudio initialisiert")
                return
            except ImportError:
                print("[STT] PyAudio is not installed")
            except Exception as e:
                print(f"[STT] PyAudio init failed: {e}")
            try:
                import sounddevice, numpy
                self.sd = sounddevice; self.np = numpy
                self.stt_backend = "sounddevice"; self.stt_available = True
                print("[STT] sounddevice initialisiert")
                return
            except ImportError:
                print("[STT] sounddevice is not installed")
            except Exception as e:
                print(f"[STT] sounddevice init failed: {e}")
            print("[STT] No audio backend. pip install pyaudio")
        except ImportError:
            print("[STT] SpeechRecognition is missing")

    def stop(self):
        """Bricht Aufnahme ab."""
        self._stop_flag.set()

    # ── Aufnahme ──────────────────────────────────────────────────────────
    def listen(self, stop_event=None) -> Optional[str]:
        self._stop_flag.clear()
        if not self.stt_available:
            return None
        lang = self.get_lang()
        if self.stt_backend == "pyaudio":
            return self._listen_pyaudio(lang, stop_event)
        elif self.stt_backend == "sounddevice":
            return self._listen_sounddevice(lang, stop_event)
        return None

    def _listen_pyaudio(self, lang, stop_event) -> Optional[str]:
        import speech_recognition as sr
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.3)
                print(f"[STT] Listening ({lang})...")
                audio = self.recognizer.listen(source, timeout=STT_LISTEN_TIMEOUT, phrase_time_limit=STT_PHRASE_LIMIT)
                if (stop_event and stop_event.is_set()) or self._stop_flag.is_set():
                    return None
                print("[STT] Verarbeite...")
                return self.recognizer.recognize_google(audio, language=lang)
        except Exception as e:
            name = type(e).__name__
            if "WaitTimeoutError" not in name and "UnknownValueError" not in name:
                print(f"[STT] Error: {e}")
            return None

    def _listen_sounddevice(self, lang, stop_event) -> Optional[str]:
        import speech_recognition as sr, io, wave
        try:
            fs, duration = 16000, 8
            print(f"[STT] Listening via sounddevice ({lang})...")
            recording = self.sd.rec(int(duration*fs), samplerate=fs, channels=1, dtype='int16')
            for _ in range(duration*10):
                time.sleep(0.1)
                if (stop_event and stop_event.is_set()) or self._stop_flag.is_set():
                    self.sd.stop(); return None
            self.sd.wait()
            buf = io.BytesIO()
            with wave.open(buf,'wb') as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(fs)
                wf.writeframes(recording.tobytes())
            buf.seek(0)
            with sr.AudioFile(buf) as src:
                data = self.recognizer.record(src)
            return self.recognizer.recognize_google(data, language=lang)
        except Exception as e:
            print(f"[STT] Error: {e}"); return None

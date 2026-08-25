"""
JARVIS Copilot — Desktop-Overlay (v2.7 #1)
==========================================
A small, always-visible window showing that My Jarvis is driving the PC.

- Stays above every other window (``-topmost``), semi-transparent, in the My Jarvis style.
- Shows the logo/header, "My Jarvis is Controlling your PC", the current step
  (updated live) and a red stop button.
- Runs in its OWN thread (tkinter needs its own event loop) and is fed status
  updates through a thread-safe queue.
- If tkinter is missing (headless), everything behaves as a no-op.
"""

import os
import queue
import logging
import threading

logger = logging.getLogger("jarvis.copilot.overlay")

# Optional logo path (PNG/GIF – supported by tk.PhotoImage). Fallback: text.
_LOGO_CANDIDATES = (
    os.path.join("gui", "assets", "jarvis.png"),
    os.path.join("gui", "assets", "logo.png"),
    os.path.join("assets", "jarvis.png"),
    os.path.join("assets", "logo.png"),
)

# Farben (JARVIS-Stil: dunkel + Cyan-Akzent)
_BG     = "#0a0e14"
_FG     = "#e6f1ff"
_ACCENT = "#36d1ff"
_STOP   = "#ff3b3b"

_WIN_W, _WIN_H = 320, 156


class CopilotOverlay:
    """A thread-safe desktop overlay for the copilot.

    Public API (callable from any thread):
        show(task)          – show the window (starts the tkinter thread)
        update_status(text) – update the current step
        hide()              – close the window
    """

    def __init__(self, on_stop=None):
        self._on_stop    = on_stop or (lambda: None)
        self._q          = queue.Queue()
        self._thread     = None
        self._root       = None
        self._status_var = None
        self._logo_ref   = None          # keep the PhotoImage reference (or the GC takes it)
        self._close      = threading.Event()
        self._visible    = False

    # ── the public, thread-safe API ──────────────────────────────────────────
    def show(self, task: str = ""):
        if self._visible:
            self.update_status(("New task: " + task) if task else "…")
            return
        self._close.clear()
        self._visible = True
        self._thread = threading.Thread(target=self._run, args=(task,), daemon=True)
        self._thread.start()

    def update_status(self, text: str):
        if not text:
            return
        try:
            self._q.put_nowait(("status", str(text)))
        except Exception:  # noqa: BLE001
            pass

    def hide(self):
        if not self._visible:
            return
        self._visible = False
        self._close.set()
        try:
            self._q.put_nowait(("close", ""))
        except Exception:  # noqa: BLE001
            pass

    # ── tkinter-Thread ────────────────────────────────────────────────────────
    def _run(self, task):
        try:
            import tkinter as tk
        except Exception as e:  # noqa: BLE001
            logger.info("[Overlay] tkinter is not available: %s", e)
            self._visible = False
            return

        try:
            root = tk.Tk()
            self._root = root
            root.title("My Jarvis Copilot")
            root.overrideredirect(True)            # no standard title bar
            root.attributes("-topmost", True)      # always on top
            try:
                root.attributes("-alpha", 0.93)    # leicht transparent
            except Exception:  # noqa: BLE001
                pass
            root.configure(bg=_BG)

            sw = root.winfo_screenwidth()
            x = max(0, (sw - _WIN_W) // 2)         # centred at the top (not in a corner!)
            root.geometry(f"{_WIN_W}x{_WIN_H}+{x}+24")

            frame = tk.Frame(root, bg=_BG, highlightbackground=_ACCENT,
                             highlightthickness=1)
            frame.pack(fill="both", expand=True, padx=2, pady=2)

            # Logo / Header
            logo = self._load_logo(tk)
            if logo is not None:
                self._logo_ref = logo
                tk.Label(frame, image=logo, bg=_BG).pack(pady=(10, 0))
            else:
                tk.Label(frame, text="◉ JARVIS", bg=_BG, fg=_ACCENT,
                         font=("Segoe UI", 16, "bold")).pack(pady=(10, 0))

            tk.Label(frame, text="is Controlling your PC", bg=_BG, fg=_FG,
                     font=("Segoe UI", 11)).pack()

            self._status_var = tk.StringVar(value="Starting…")
            tk.Label(frame, textvariable=self._status_var, bg=_BG, fg=_ACCENT,
                     font=("Segoe UI", 9), wraplength=_WIN_W - 30,
                     justify="center").pack(pady=(6, 6))

            tk.Button(frame, text="■  STOPP", bg=_STOP, fg="white",
                      activebackground="#cc0000", activeforeground="white",
                      relief="flat", font=("Segoe UI", 9, "bold"),
                      cursor="hand2", command=self._handle_stop).pack(pady=(0, 8))

            if task:
                self._status_var.set("Task: " + (task[:60]))

            self._bind_drag(root, frame)
            root.after(120, self._poll)
            root.mainloop()
        except Exception as e:  # noqa: BLE001
            logger.info("[Overlay] Error in the tkinter thread: %s", e)
        finally:
            self._root = None
            self._status_var = None
            self._visible = False

    def _poll(self):
        """Reads the status queue and closes the window when asked to."""
        root = self._root
        if root is None:
            return
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "close":
                    try:
                        root.destroy()
                    except Exception:  # noqa: BLE001
                        pass
                    return
                if kind == "status" and self._status_var is not None:
                    self._status_var.set(payload)
        except queue.Empty:
            pass
        if self._close.is_set():
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
            return
        root.after(120, self._poll)

    def _handle_stop(self):
        if self._status_var is not None:
            self._status_var.set("Stopping…")
        try:
            self._on_stop()
        except Exception as e:  # noqa: BLE001
            logger.info("[Overlay] on_stop error: %s", e)

    def _load_logo(self, tk):
        for p in _LOGO_CANDIDATES:
            if os.path.isfile(p):
                try:
                    img = tk.PhotoImage(file=p)
                    w = img.width()
                    if w > 72:
                        img = img.subsample(max(1, w // 72), max(1, w // 72))
                    return img
                except Exception:  # noqa: BLE001
                    continue
        return None

    def _bind_drag(self, root, widget):
        """Lets the window be dragged with the mouse despite having no title bar."""
        state = {"x": 0, "y": 0}

        def start(e):
            state["x"], state["y"] = e.x, e.y

        def move(e):
            nx = root.winfo_x() + (e.x - state["x"])
            ny = root.winfo_y() + (e.y - state["y"])
            root.geometry(f"+{nx}+{ny}")

        for w in (root, widget):
            w.bind("<Button-1>", start)
            w.bind("<B1-Motion>", move)

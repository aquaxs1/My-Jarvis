# JARVIS — Just A Rather Very Intelligent System

Voice-first AI assistant with screen vision, memory, system control and smart home integration.

JARVIS runs locally on your PC, opens a browser interface and connects to an AI API of your choice (Claude, ChatGPT, Gemini, Mistral, NVIDIA NIM or local Ollama). Everything is controlled by voice, text or keyboard shortcut.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Starting JARVIS](#starting-jarvis)
4. [Initial Setup](#initial-setup)
5. [The Interface](#the-interface)
6. [All Buttons and Controls](#all-buttons-and-controls)
7. [Settings](#settings)
8. [All Features Overview](#all-features-overview)
9. [Keyboard Shortcuts](#keyboard-shortcuts)
10. [Project Structure](#project-structure)
11. [Troubleshooting](#troubleshooting)

---

## Requirements

- **Python 3.10+** (python.org — check "Add Python to PATH" during installation)
- **Operating System:** Windows 10/11, macOS or Linux
- **API Key** from one of the supported providers (or local Ollama)
- **Microphone** (for voice input, optional)
- **Speakers** (for voice output, optional)

### Supported AI Providers

| Provider | Model | Key Format |
|----------|-------|------------|
| Anthropic Claude | claude-sonnet-4-20250514 | `sk-ant-api03-...` |
| OpenAI ChatGPT | gpt-4o-mini | `sk-...` |
| Google Gemini | gemini-1.5-flash | `AIza...` |
| NVIDIA NIM | llama-3.1-70b | `nvapi-...` |
| Mistral AI | mistral-large-latest | API Key |
| Local (Ollama) | llama3, mistral, etc. | No key needed |

---

## Installation

### Windows — ready-made build (no Python needed)

Download [`my-jarvis-windows-x64.zip`](https://github.com/aquaxs1/My-Jarvis/releases/latest/download/my-jarvis-windows-x64.zip)
from the [latest release](https://github.com/aquaxs1/My-Jarvis/releases/latest),
unpack it, and run `jarvis.exe` from the folder. Keep the folder together —
`jarvis.exe` loads the rest of it at startup.

The build is not code-signed, so SmartScreen warns on first launch: choose
**More info → Run anyway**. See
[Windows Defender flags the download](#windows-defender-flags-the-download) if a
scanner objects.

### Windows — from source

```
git clone <repository-url> jarvis
cd jarvis
install.bat
```

The installer checks Python, installs all packages and creates the necessary directories.

**Manual installation (if the installer doesn't work):**

```
cd jarvis
pip install -r requirements.txt
```

For PyAudio on Windows (microphone support):
```
pip install pyaudio
```
If that fails:
```
pip install pipwin
pipwin install pyaudio
```
JARVIS also works without PyAudio — `sounddevice` is used as a fallback for the microphone.

### macOS

```
git clone <repository-url> jarvis
cd jarvis
brew install portaudio
pip3 install -r requirements.txt
```

### Linux (Debian/Ubuntu)

```
git clone <repository-url> jarvis
cd jarvis
sudo apt-get install -y portaudio19-dev python3-pyaudio espeak-ng flac
pip3 install -r requirements.txt
```

Or use the installer:
```
chmod +x install.sh
./install.sh
```

### Optional Packages

These packages extend JARVIS with additional features. JARVIS starts without them — the respective feature is simply skipped.

| Package | Feature | Install |
|---------|---------|---------|
| `black` | Python code formatting | `pip install black` |
| `google-api-python-client` | Google Calendar | `pip install google-api-python-client google-auth-oauthlib` |
| `dateparser` | Natural language date parsing | `pip install dateparser` |
| `todoist-api-python` | Todoist integration | `pip install todoist-api-python` |
| `notion-client` | Notion integration | `pip install notion-client` |
| `feedparser` | RSS news for briefing | `pip install feedparser` |
| `pdfplumber` | PDF analysis | `pip install pdfplumber` |
| `python-docx` | Read Word documents | `pip install python-docx` |
| `youtube-transcript-api` | YouTube summaries | `pip install youtube-transcript-api` |
| `pvporcupine` | Precise wake word detection | `pip install pvporcupine` |

---

## Starting JARVIS

On Windows, double-click:

```
start.bat
```

On macOS/Linux:
```
./start.sh
```

Or start it directly:

```
python jarvis.py       (Windows)
python3 jarvis.py      (macOS/Linux)
```

The launcher scripts do the same thing as `python jarvis.py`, except that they
keep the console window open if something goes wrong. Started by double-click,
`jarvis.py` closes its window the instant it fails, which makes a missing
package look like "a black window opens and nothing happens".

JARVIS starts a local web server and automatically opens the browser at `http://127.0.0.1:8765`. The boot screen shows the initialization progress. Leave the console window open while you use JARVIS — closing it stops the assistant.

If port 8765 is in use, JARVIS automatically tries the next ports (8766, 8767, ...) for both the web server and the WebSocket, and the interface asks the backend which ports it settled on.

The first-time configuration happens in the interface, not in the console. To
run the console wizard instead:

```
python jarvis.py --setup
```

---

## Initial Setup

On first start, a configuration dialog appears in the browser:

1. **Choose title** — how JARVIS addresses you (Sir, Boss, Bro, Ma'am or none)
2. **Choose speech style** — Professional, Normal or Casual
3. **Choose AI provider** — click the tile and enter your API key
4. **Enter location** — used for weather and local information
5. Click **START JARVIS**

The API key is stored encrypted (Windows: DPAPI/Credential Manager, fallback: Fernet encryption). It is never stored as plain text in the config file.

Configuration is saved at `~/.jarvis/config.json`.

---

## The Interface

The interface consists of three areas:

```
+------------------+---------------------------+------------------+
|                  |                           |                  |
|    SIDEBAR       |       CHAT AREA           |   RIGHT PANEL    |
|                  |                           |                  |
| - Thought process|  - Orb header (status)    | - To-Do list     |
| - Metrics        |  - Chat messages          | - Memory         |
| - Account        |  - Code blocks            | - Suggestions    |
|                  |  - Input bar              | - Session stats  |
|                  |                           |                  |
+------------------+---------------------------+------------------+
```

### Orb Header (top center)

The animated orb shows the current status:

| Orb Color | Meaning |
|-----------|---------|
| Teal (pulsing) | Ready |
| Teal (glowing) | Listening |
| Orange (rotating) | Thinking / processing |
| Teal (pulsing) | Speaking |
| Red | Error / Kill-Switch active |

Next to it: current time, selected AI provider and the Kill-Switch button.

---

## All Buttons and Controls

### Input Bar (bottom center)

| Element | Function |
|---------|----------|
| **Microphone button** (left) | Hold to speak. Release ends the recording and sends the recognized text. |
| **Eye button** (Live Vision) | Click to toggle live vision on/off. When active: JARVIS observes the screen every 2–3 seconds and gives voice feedback. Pulses teal when active. |
| **Text field** | Type text and send with Enter or the send button. Max. 2000 characters. |
| **Send button** (arrow, right) | Sends the text message. Turns teal when text is entered. |

### Sidebar (left)

| Element | Function |
|---------|----------|
| **JARVIS Logo + Version** | Shows the current version (v2.8). |
| **NEW SESSION** | Reloads the page and starts a fresh chat session. |
| **Thought process** | Shows the AI agent's thinking steps in real time. Each step has a label (ANALYSIS, PLANNING, EXECUTION, etc.) and a short description. |
| **Metrics** | Tokens: Estimated token count for the session. Security: ACTIVE or BLOCKED. Memory: Number of stored memories. Routines: Number of stored routines. |
| **Account area** (bottom) | Click to open the account menu with settings, language, help. The gear icon opens settings directly. |

### Right Panel

Three tabs, switchable by clicking:

| Tab | Content |
|-----|---------|
| **TODO** | Task list. Add, check off, delete tasks. "START TO-DO" lets JARVIS work through open tasks one by one. On errors it stops and shows a "PROBLEM SOLVED" button. |
| **MEMORY** | All stored memories (Key=Value). Deletable via the X button. Refreshable via the reload button. |
| **SUGGESTIONS** | Context-aware suggestions. Clicking a suggestion sends it as a message. |

### Chat Area

| Element | Function |
|---------|----------|
| **Messages** | JARVIS messages (left, with orb avatar) and user messages (right, with U avatar). Hover shows copy and retry buttons. |
| **Code blocks** | Automatically tested and formatted code with language label and COPY button. Syntax highlighting for Python, JavaScript and more. |
| **Thinking drawer** | Appears while JARVIS is thinking and shows the current processing steps. |
| **Setup banner** | Yellow banner at the top when no API key is set. Click "SETTINGS" to open the config. |

### Kill-Switch Overlay

Displayed over the entire interface when the kill switch is active. Red glitch effect with "KILL-SWITCH ACTIVATED". Deactivate with the button or Ctrl+Alt+J.

### Screenshot Countdown

5-second countdown during screen analysis. Gives you time to arrange the desired screen content.

---

## Settings

Open via the gear icon in the sidebar or Ctrl+Comma.

### Title

How JARVIS addresses you. Presets: **Sir**, **Boss**, **Bro**, **Ma'am**, **None**. Add custom titles via **+** (e.g. Captain, Chief, Master).

### Speech Style

| Mode | Description |
|------|-------------|
| **Professional** | Precise answers, technical terms welcome |
| **Normal** | Friendly, clearly understandable |
| **Casual** | Casual slang (bro, no cap, etc.) |

### Language

Switchable between: German, English, French, Spanish, Italian, Turkish. Changes both AI responses and speech recognition/output.

### Voice Output (TTS)

Toggle on/off. When active, JARVIS reads its answers aloud via speech synthesis.

**TTS Voice:** Dropdown with all voices available on the system. "Default" automatically selects a suitable voice for the set language.

### Show Suggestions

Toggle on/off. When active, JARVIS shows context-aware suggestions in the right panel and at startup.

### Light Mode

Switches between dark (default) and light theme. The setting persists across sessions.

### Activation

| Toggle | Function |
|--------|----------|
| **Wake Word ("Hey Jarvis")** | JARVIS listens in the background for "Hey Jarvis". When detected, voice recording starts automatically — like pressing the microphone button. Uses `pvporcupine` (if installed) or Google Speech Recognition as fallback. |
| **Clap Activation (2x Clap)** | Two quick clapping sounds within 1 second activate voice input. Hands-free alternative to the microphone button. |
| **Sensitivity** (slider) | Only visible when clap activation is on. Controls the RMS threshold (0.05 = very sensitive, 0.9 = insensitive). Default: 0.30. |
| **CALIBRATE** | Measures background noise for 3 seconds and sets the optimal threshold automatically. |

### AI Provider

Six providers to choose from. Click a tile and enter the API key:

| Provider | Additional Fields |
|----------|------------------|
| **Anthropic Claude** | API Key |
| **OpenAI ChatGPT** | API Key |
| **Google Gemini** | API Key |
| **NVIDIA NIM** | API Key + Model name |
| **Mistral AI** | API Key |
| **Local (Ollama)** | URL (default: http://localhost:11434) + Model name |

The status below the key field shows "API Key active" (green) or "No API Key set" (orange).

### Location

Used for weather queries, local news and location-based information. JARVIS never asks for your location — it always uses this value.

---

## All Features Overview

### Core Features

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Chat** | Natural language conversation in your set language | "Explain quantum computing" |
| **Voice input** | Hold microphone button, speech is recognized and sent as text | (hold microphone button) |
| **Voice output** | JARVIS reads answers aloud (can be disabled) | (automatic) |
| **Multi-provider** | 6 AI providers supported, switchable at any time | (in settings) |
| **Kill-Switch** | Ctrl+Alt+J immediately stops all running actions, speech and commands | Ctrl+Alt+J |
| **Memory** | JARVIS remembers information permanently (encrypted) | "Remember that my name is Sebastian" |

### Screen & Vision

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Screenshot analysis** | 5-second countdown, screenshot, Vision API analyzes the screen | "Look at my screen" |
| **Live vision** | Continuously observes the screen (every 2–3s), gives feedback on changes | Eye button in the composer |
| **PC control** | Mouse clicks, keyboard input, opening programs (with security checks) | "Open Chrome and search for weather" |

### Code Processing

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Syntax test** | Python code checked with `py_compile`, JavaScript with `node --check` | (automatic for code responses) |
| **Auto-correction** | On syntax errors, JARVIS tries up to 3 times to fix the code | (automatic) |
| **Auto-formatting** | Python with `black`/`autopep8`, JS/HTML/CSS with `prettier` | (automatic) |
| **Copy button** | Every code block has a COPY button | (in chat) |

### Wake Word & Clap

| Feature | Description | Activation |
|---------|-------------|------------|
| **"Hey Jarvis"** | Background voice activation (CPU-efficient) | Settings > Activation |
| **2x Clap** | Alternative hands-free activation via clapping | Settings > Activation |
| **Calibration** | Automatic adjustment to background noise | CALIBRATE button |

### Calendar (Google Calendar)

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Get events** | Shows today's/tomorrow's/weekly events | "What do I have tomorrow?" |
| **Create event** | Creates a new event with natural language time | "Create an event for Friday 2pm: Meeting with team" |

Requirement: Place `google_credentials.json` in `~/.jarvis/` (Google Cloud Console > Enable Calendar API > Create OAuth credentials). On first use, the browser opens for Google sign-in.

### Task Management (Todoist / Notion)

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Read tasks** | Shows open tasks from Todoist or Notion | "What are my open tasks?" |
| **Create task** | Creates a new task | "Add to Todoist: reply to email" |
| **Complete task** | Marks a task as done | (via Todoist/Notion directly) |

Requirement: Enter API keys in the config (`todoist_api_key`, `notion_api_key`, `notion_database_id`).

### Email

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Fetch unread** | Gets the latest unread emails via IMAP | "Summarize my emails" |
| **Summary** | AI summarizes all unread emails compactly | "What's new in my inbox?" |
| **Draft reply** | Creates a draft — only sent after confirmation | "Write a reply to Max" |

Requirement: Enter IMAP/SMTP server, email address and app password in the config (`email_imap_server`, `email_address`, `email_app_password`).

**Important:** Emails are never sent automatically. JARVIS always shows the draft first and waits for explicit confirmation.

### Daily Briefing

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Manual briefing** | Compact briefing with weather, news, events, tasks | "Give me my briefing" |
| **Automatic briefing** | Daily at the set time (default: 08:00) | (automatic) |

Content: Weather for your location, top news (RSS: BBC, Reuters), today's events (if calendar configured), open tasks (if tasks configured). Sources that are not set up are skipped.

### PDF & Document Analysis

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Read PDF** | Extracts text from PDFs (including tables/layouts) | "Summarize C:\Documents\report.pdf" |
| **Read Word** | Reads .docx files | "What does contract.docx say?" |
| **Text files** | TXT, MD, CSV, JSON, XML, HTML | "Read the file.txt" |
| **Chunking** | Long documents are automatically split into sections | (automatic) |

### Smart Home (Home Assistant)

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Lights** | On/off, adjust brightness | "Turn on the lights" |
| **Temperature** | Control thermostat | "Set the temperature to 22 degrees" |
| **Media** | Start/stop music | "Play music" |
| **Status** | Overview of all devices | "Smart home status" |

Requirement: Enter Home Assistant URL and Long-Lived Access Token in the config (`ha_url`, `ha_token`). Alternative: direct Philips Hue bridge connection (`hue_bridge_ip`).

### YouTube Summaries

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Auto-detection** | YouTube links in messages are automatically detected | "https://youtube.com/watch?v=..." |
| **Transcript** | Subtitles are fetched (English preferred, then first available) | (automatic) |
| **Summary** | AI creates a compact summary of the video content | "Summarize this video: [link]" |

Short videos (under 15 min.) are summarized directly; longer ones are split into 5-minute sections.

### Social Media Drafts

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **LinkedIn post** | Professional, 150–300 words, hashtags | "Write a LinkedIn post about AI in healthcare" |
| **Twitter/X thread** | 5–8 tweets, numbered (1/N), max. 280 characters per tweet | "Create a Twitter thread on productivity tips" |
| **Newsletter** | Intro, main section, call-to-action, max. 500 words | "Write a newsletter about remote work" |
| **Revise** | Adjust tone, length or style | "Make the post shorter" |

### Proactive Suggestions

JARVIS suggests sensible next steps after actions:

- In the morning: "Give me my briefing"
- After creating an event: "Should I set a reminder?"
- After code: "Should I test the code?"
- In the evening: "Summarize my day"

Can be disabled in settings (toggle "Show suggestions").

### Deadline Warnings

| Warning Level | Timing | Display |
|---------------|--------|---------|
| Info | 24 hours before | Chat message |
| Warning | 1 hour before | Yellow highlight |
| Critical | 15 minutes before | Red warning + sound |

Sources: Google Calendar events, Todoist/Notion tasks, deadlines mentioned in conversations ("This needs to be done by Friday").

### Research Assistant

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Fetch articles** | RSS feeds, arXiv papers, Hacker News top stories | "What's new in research?" |
| **Configure topics** | Set custom topics for arXiv search | Config: `research_topics: ["AI", "Python"]` |
| **Article details** | Full summary of an article | "More about article 2" |

### Decision Helper

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Pro/con analysis** | Structured analysis with clear recommendation | "Should I learn Python or Rust?" |
| **Context-aware** | Uses stored user preferences | "What's better for me: freelancing or employment?" |

### Finance Tracker

| Feature | Description | Example Input |
|---------|-------------|---------------|
| **Log expense** | Recognize and save amount and category | "I spent 45 euros on groceries" |
| **Set budget** | Monthly limit per category | "Set my transport budget to 100 euros" |
| **Overview** | Summary by period and category | "How much have I spent this week?" |
| **Budget warning** | Automatically at 80% and 100% usage | (automatic) |

Categories: Groceries, Transport, Entertainment, Housing, Clothing, Health, Education, Other. The category is automatically recognized from the description.

### To-Do Execution

The to-do list in the right panel is not just a checklist — JARVIS can work through tasks autonomously one by one:

1. Add tasks via **+ Add task**
2. Click **START TO-DO**
3. JARVIS works through each task and marks it as done
4. On problems it stops and shows an error message
5. After resolving: click **PROBLEM SOLVED** to continue
6. **CANCEL** stops execution at any time

### Security System

| Feature | Description |
|---------|-------------|
| **Kill-Switch** | Ctrl+Alt+J immediately stops EVERYTHING (speech, commands, vision, to-dos) |
| **Security classification** | Every request is checked for risk, dangerous actions are blocked |
| **Permission system** | For risky actions JARVIS asks for permission first |
| **API key encryption** | Keys are stored via DPAPI (Windows) or Fernet encryption |
| **Program whitelist** | Only safe programs can be opened |
| **Injection protection** | Shell injection attempts are detected and blocked |

---

## Keyboard Shortcuts

| Shortcut | Function |
|----------|----------|
| **Ctrl+Alt+J** | Kill-Switch (toggle: activate / deactivate) |
| **Ctrl+K** | Focus on the input field |
| **Enter** | Send message (when input field is focused) |
| **Esc** | Leave input field |

---

## Project Structure

```
jarvis/
  jarvis.py                    Main program, starts all modules
  requirements.txt             Python dependencies
  install.bat                  Windows installer
  install.sh                   macOS/Linux installer
  start.bat                    Windows launcher (keeps the window open on error)
  start.sh                     macOS/Linux launcher
  core/
    config.py                  Configuration, encrypted key storage
    brain.py                   AI logic, request router, all agents
    speech.py                  Voice input (STT) and voice output (TTS)
    gui_server.py              WebSocket server, HTTP server, event handling
    executor.py                PC control (mouse, keyboard, programs)
    safety.py                  Security checks, blacklist, risk score
    screen.py                  Screenshot and window detection
    code_processor.py          Test, format and correct code
    wake_word.py               Wake word + clap detection
    calendar_integration.py    Google Calendar API
    tasks.py                   Todoist + Notion integration
    email_manager.py           Email via IMAP/SMTP
    briefing.py                Daily briefing (weather, news, etc.)
    document_reader.py         Read and chunk PDF, DOCX, TXT
    smarthome.py               Home Assistant + Philips Hue
    youtube.py                 YouTube transcripts and summaries
    proactive.py               Proactive suggestions
    deadlines.py               Deadline monitoring and warnings
    research.py                Research briefing (RSS, arXiv, HN)
    finance.py                 Expense tracking and budgets
    orchestrator.py            Multi-agent framework
  memory/
    memory_store.py            Encrypted memory store
  gui/
    index.html                 Complete frontend (HTML + CSS + JS)
  agents/
    __init__.py                Agent module (extension point)
```

All user data is stored at `~/.jarvis/`:
- `config.json` — configuration (API key as sentinel, not plain text)
- `.api_key.enc` — Fernet-encrypted API key
- `.config_salt` — salt for key derivation
- `google_token.json` — Google OAuth token
- `finance.json` — finance data
- `memory/` — encrypted memories, history, routines

---

## Troubleshooting

### "API Key missing" banner appears

Open settings (gear icon), select your AI provider and enter the API key.

### Microphone not working

```
pip install pyaudio
```
If that fails on Windows:
```
pip install pipwin && pipwin install pyaudio
```
JARVIS will use `sounddevice` as a fallback.

### No sound for voice output

Check if TTS is enabled in settings. On Linux, install `espeak-ng` if needed:
```
sudo apt-get install espeak-ng
```

### Windows Defender flags the download

Builds up to v2.8.2 shipped as a single self-extracting `.exe` and were flagged
as `Trojan:Win32/Sabsik.TE.A!ml`. That was a false positive on the packing
format rather than on anything in the code: PyInstaller's onefile mode unpacks
the whole program into `%TEMP%` at every start, which is what a dropper does.
The `!ml` suffix marks it as a machine-learning verdict, not a signature match.

From v2.8.3 the download is a plain folder in a zip, so nothing unpacks itself
at runtime, and the executable carries an icon and a version resource. What
remains is the unsigned-binary warning, which only a code-signing certificate
removes.

**If you downloaded v2.8.3**, take v2.8.4 instead. The v2.8.3 zip was written
with Windows path separators, which the ZIP format does not allow: Windows
Explorer unpacks it correctly, but 7-Zip, WinRAR and non-Windows tools can turn
it into thousands of files with backslashes in their names instead of a folder.
Delete whatever it produced and unpack the new zip.

If a scanner still objects:

- Check the download against `SHA256SUMS.txt` on the release page. Every build
  is produced by the public workflow in `.github/workflows/release.yml`, from
  public source, and the run is linked on the release.
- Report the false positive to Microsoft at
  <https://www.microsoft.com/en-us/wdsi/filesubmission> — these are usually
  cleared within a few days.
- Or skip the binary entirely and install from source, which no scanner
  objects to.

### A console window opens and nothing happens

Start JARVIS through `start.bat` (Windows) or `./start.sh` (macOS/Linux)
instead of double-clicking `jarvis.py`. The window then stays open and shows
the cause. The usual ones:

- **A required package is missing.** JARVIS names it and gives you the exact
  `pip install` line. `cryptography` and `httpx` are the two that used to be
  skipped by the installer; `install.bat` now installs and verifies both.
- **The installer never finished.** Run `install.bat` again and read the
  `[6/6]` check at the end — it now fails loudly instead of reporting success.
- **An old JARVIS is still running.** Close the other console window, or see
  "Port in use" below.

### Port in use

JARVIS automatically tries the next ports for both the web server and the
WebSocket, and the interface reads the ports it actually got. If the interface
opens but stays on `GETRENNT`/`OFFLINE`, close any other running JARVIS
instance and start it again. If problems persist: stop any other application
using ports 8765-8775.

### Google Calendar "credentials missing"

1. Open Google Cloud Console
2. Create a new project
3. Enable the Calendar API
4. Create an OAuth 2.0 Client ID (Desktop application)
5. Download the JSON and save it as `google_credentials.json` in `~/.jarvis/`
6. Restart JARVIS — on first calendar access the browser opens for sign-in

### Wake word not responding

- Check if the toggle is enabled in settings
- If `pvporcupine` is not installed: fallback uses Google Speech Recognition (requires internet)
- Check microphone access (other apps that might be blocking the microphone)

### Clap detection triggers on background noise

Press the CALIBRATE button in settings (stay quiet for 3 seconds). Alternatively increase the sensitivity via the slider (higher = less sensitive).

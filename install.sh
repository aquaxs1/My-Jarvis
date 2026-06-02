#!/bin/bash
echo ""
echo " ╔══════════════════════════════════════╗"
echo " ║        J·A·R·V·I·S INSTALLER        ║"
echo " ╚══════════════════════════════════════╝"
echo ""

# Python check
if ! command -v python3 &> /dev/null; then
    echo " [FEHLER] Python3 nicht gefunden!"
    echo " macOS: brew install python3"
    echo " Linux: sudo apt install python3 python3-pip"
    exit 1
fi

echo " [1/5] Python3 gefunden: $(python3 --version)"

# System-Abhängigkeiten
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo " [2/5] Installiere System-Pakete (sudo nötig)..."
    sudo apt-get install -y portaudio19-dev python3-pyaudio espeak-ng flac 2>/dev/null || true
elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo " [2/5] Installiere System-Pakete (Homebrew)..."
    brew install portaudio 2>/dev/null || true
fi

echo " [3/5] Installiere Python-Pakete..."
pip3 install -r requirements.txt --quiet

echo " [4/5] Erstelle Verzeichnisse..."
touch core/__init__.py
touch memory/__init__.py
touch agents/__init__.py

mkdir -p ~/.jarvis/memory

echo " [5/5] Installation abgeschlossen!"
echo ""
echo " ══════════════════════════════════════"
echo "  JARVIS starten: python3 jarvis.py"
echo " ══════════════════════════════════════"
echo ""

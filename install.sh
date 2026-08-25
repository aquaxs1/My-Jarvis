#!/bin/bash
echo ""
echo " ╔══════════════════════════════════════╗"
echo " ║       M·Y   J·A·R·V·I·S  SETUP      ║"
echo " ╚══════════════════════════════════════╝"
echo ""

# Python check
if ! command -v python3 &> /dev/null; then
    echo " [ERROR] Python3 not found!"
    echo " macOS: brew install python3"
    echo " Linux: sudo apt install python3 python3-pip"
    exit 1
fi

echo " [1/5] Python3 found: $(python3 --version)"

# system dependencies
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo " [2/5] Installing system packages (sudo required)..."
    sudo apt-get install -y portaudio19-dev python3-pyaudio espeak-ng flac 2>/dev/null || true
elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo " [2/5] Installing system packages (Homebrew)..."
    brew install portaudio 2>/dev/null || true
fi

echo " [3/5] Installing Python packages..."
pip3 install -r requirements.txt --quiet

echo " [4/5] Creating directories..."
touch core/__init__.py
touch memory/__init__.py
touch agents/__init__.py

mkdir -p ~/.jarvis/memory

echo " [5/5] Installation complete!"
echo ""
echo " ══════════════════════════════════════"
echo "  Start My Jarvis: python3 jarvis.py"
echo " ══════════════════════════════════════"
echo ""

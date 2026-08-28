#!/bin/bash
# Starts My Jarvis. The interface opens in the browser; leave this terminal
# open while you use it.
cd "$(dirname "$0")" || exit 1

export JARVIS_LAUNCHER=1

if command -v python3 >/dev/null 2>&1; then
    PYCMD=python3
elif command -v python >/dev/null 2>&1; then
    PYCMD=python
else
    echo " [ERROR] Python 3 not found."
    echo " macOS: brew install python3"
    echo " Linux: sudo apt install python3 python3-pip"
    exit 1
fi

echo " Starting My Jarvis... the interface opens in your browser."
echo ""
"$PYCMD" jarvis.py "$@"
CODE=$?

if [ "$CODE" -ne 0 ]; then
    echo ""
    echo " My Jarvis stopped with error code $CODE. The cause is above."
fi
exit $CODE

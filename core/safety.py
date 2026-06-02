"""
JARVIS Sicherheitswächter
Schützt den PC vor schädlichen Aktionen
"""

import re
from typing import Tuple

# Definitiv verbotene Aktionen
BLACKLIST_COMMANDS = [
    r"rm\s+-rf\s+/",          # Linux: alles löschen
    r"format\s+c:",           # Windows: Festplatte formatieren
    r"del\s+/[sf]",           # Windows: Systemdateien löschen
    r"shutdown\s+/[sr]",      # Neustart/Shutdown ohne Erlaubnis
    r"reg\s+delete.*hklm",    # Windows Registry beschädigen
    r"netsh.*firewall.*disable", # Firewall deaktivieren
    r"bcdedit",               # Boot-Konfiguration ändern
    r"diskpart",              # Festplattenpartitionierung
    r"taskkill.*system",      # Systemprozesse killen
    r"wmic.*delete",          # WMI-Löschaktionen
    r"cipher\s+/w",           # Sicheres Überschreiben
    r"sfc\s+/scannow",        # Systemdatei-Checker (braucht Admin)
]

# Aktionen die Erlaubnis brauchen
PERMISSION_REQUIRED = [
    r"\.(exe|msi|bat|cmd|ps1|sh)$",  # Ausführbare Dateien
    r"wget|curl.*-[oO]",              # Downloads
    r"pip install|npm install",       # Paketinstallationen
    r"sudo|runas",                    # Elevated privileges
    r"regedit|registry",              # Registry
    r"payment|credit card|paypal",    # Zahlungen
]


class SafetyGuard:
    def __init__(self):
        self.blocked_count = 0
        self.allowed_count = 0

    def check_command(self, command: str) -> Tuple[bool, str]:
        """
        Prüft ob ein Befehl sicher ist.
        Returns: (ist_sicher, grund_falls_nicht_sicher)
        """
        command_lower = command.lower()

        # Blacklist-Check
        for pattern in BLACKLIST_COMMANDS:
            if re.search(pattern, command_lower):
                self.blocked_count += 1
                return False, f"Dieser Befehl wurde blockiert da er Systemschäden verursachen könnte: '{pattern}'"

        # Mehrfache Sicherheitsprüfung für mittlere Risiken
        risk_score = self._calculate_risk(command_lower)

        if risk_score >= 8:
            self.blocked_count += 1
            return False, f"Risikowert zu hoch ({risk_score}/10). Aktion blockiert."

        self.allowed_count += 1
        return True, ""

    def _calculate_risk(self, command: str) -> int:
        """Berechnet Risikowert 0-10"""
        score = 0

        # Datei-Löschaktionen
        if any(kw in command for kw in ["delete", "remove", "del ", "rm "]):
            score += 3

        # System-Verzeichnisse
        if any(kw in command for kw in ["system32", "windows", "/etc", "/usr/bin", "program files"]):
            score += 4

        # Netzwerk-Aktionen
        if any(kw in command for kw in ["netstat", "iptables", "firewall"]):
            score += 2

        # Kritische Programme
        if any(kw in command for kw in ["regedit", "gpedit", "msconfig"]):
            score += 3

        # Bulk-Operationen
        if any(kw in command for kw in ["*.*", "/s ", "-r ", "--recursive"]):
            score += 2

        return min(score, 10)

    def needs_permission(self, action: str) -> Tuple[bool, str]:
        """Prüft ob Aktion Nutzer-Erlaubnis braucht"""
        action_lower = action.lower()

        for pattern in PERMISSION_REQUIRED:
            if re.search(pattern, action_lower):
                return True, f"Diese Aktion erfordert Ihre Erlaubnis: {action[:100]}"

        return False, ""

    def is_file_edit_safe(self, filepath: str, user_mentioned: bool) -> Tuple[bool, str]:
        """Prüft ob Datei-Bearbeitung sicher ist"""
        if not user_mentioned:
            return False, f"Ich benötige Ihre Erlaubnis um diese Datei zu bearbeiten: {filepath}"

        # Systemdateien nie bearbeiten
        dangerous_paths = [
            "windows/system32", "windows/system64", "/etc/", "/usr/",
            "program files", "appdata/roaming/microsoft",
            ".bashrc", ".profile", "hosts"
        ]

        for dp in dangerous_paths:
            if dp.lower() in filepath.lower():
                return False, f"Systemdatei kann nicht bearbeitet werden: {filepath}"

        return True, ""

    def get_stats(self) -> dict:
        return {
            "blocked": self.blocked_count,
            "allowed": self.allowed_count
        }

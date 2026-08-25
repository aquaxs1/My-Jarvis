"""
My Jarvis safety guard
Protects the PC against harmful actions
"""

import re
from typing import Tuple

# actions that are forbidden outright
BLACKLIST_COMMANDS = [
    r"rm\s+-rf\s+/",          # Linux: delete everything
    r"format\s+c:",           # Windows: format the drive
    r"del\s+/[sf]",           # Windows: delete system files
    r"shutdown\s+/[sr]",      # restart/shutdown without permission
    r"reg\s+delete.*hklm",    # damage the Windows registry
    r"netsh.*firewall.*disable", # disable the firewall
    r"bcdedit",               # change the boot configuration
    r"diskpart",              # disk partitioning
    r"taskkill.*system",      # kill system processes
    r"wmic.*delete",          # WMI delete operations
    r"cipher\s+/w",           # secure overwrite
    r"sfc\s+/scannow",        # system file checker (needs admin)
]

# actions that need permission
PERMISSION_REQUIRED = [
    r"\.(exe|msi|bat|cmd|ps1|sh)$",  # executable files
    r"wget|curl.*-[oO]",              # Downloads
    r"pip install|npm install",       # package installs
    r"sudo|runas",                    # Elevated privileges
    r"regedit|registry",              # Registry
    r"payment|credit card|paypal",    # payments
]


class SafetyGuard:
    def __init__(self):
        self.blocked_count = 0
        self.allowed_count = 0

    def check_command(self, command: str) -> Tuple[bool, str]:
        """
        Checks whether a command is safe.
        Returns: (is_safe, reason_if_not_safe)
        """
        command_lower = command.lower()

        # blacklist check
        for pattern in BLACKLIST_COMMANDS:
            if re.search(pattern, command_lower):
                self.blocked_count += 1
                return False, f"This command was blocked because it could damage the system: '{pattern}'"

        # a further safety check for medium risks
        risk_score = self._calculate_risk(command_lower)

        if risk_score >= 8:
            self.blocked_count += 1
            return False, f"Risk score too high ({risk_score}/10). Action blocked."

        self.allowed_count += 1
        return True, ""

    def _calculate_risk(self, command: str) -> int:
        """Calculates a risk score from 0 to 10."""
        score = 0

        # file deletions
        if any(kw in command for kw in ["delete", "remove", "del ", "rm "]):
            score += 3

        # system directories
        if any(kw in command for kw in ["system32", "windows", "/etc", "/usr/bin", "program files"]):
            score += 4

        # network actions
        if any(kw in command for kw in ["netstat", "iptables", "firewall"]):
            score += 2

        # critical programs
        if any(kw in command for kw in ["regedit", "gpedit", "msconfig"]):
            score += 3

        # bulk operations
        if any(kw in command for kw in ["*.*", "/s ", "-r ", "--recursive"]):
            score += 2

        return min(score, 10)

    def needs_permission(self, action: str) -> Tuple[bool, str]:
        """Checks whether an action needs the user's permission."""
        action_lower = action.lower()

        for pattern in PERMISSION_REQUIRED:
            if re.search(pattern, action_lower):
                return True, f"This action needs your permission: {action[:100]}"

        return False, ""

    def is_file_edit_safe(self, filepath: str, user_mentioned: bool) -> Tuple[bool, str]:
        """Checks whether editing a file is safe."""
        if not user_mentioned:
            return False, f"I need your permission to edit this file: {filepath}"

        # never edit system files
        dangerous_paths = [
            "windows/system32", "windows/system64", "/etc/", "/usr/",
            "program files", "appdata/roaming/microsoft",
            ".bashrc", ".profile", "hosts"
        ]

        for dp in dangerous_paths:
            if dp.lower() in filepath.lower():
                return False, f"This system file cannot be edited: {filepath}"

        return True, ""

    def get_stats(self) -> dict:
        return {
            "blocked": self.blocked_count,
            "allowed": self.allowed_count
        }

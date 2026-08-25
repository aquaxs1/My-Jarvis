"""
My Jarvis email manager
- IMAP for reading (unread emails)
- SMTP for replies (only after confirmation)
"""
import imaplib
import smtplib
import email
import email.utils
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from typing import Optional

logger = logging.getLogger(__name__)


class EmailManager:
    def __init__(self, config: dict):
        self.config = config
        self._pending_draft = None

    @property
    def is_configured(self) -> bool:
        return bool(self.config.get("email_address") and
                    self.config.get("email_app_password") and
                    self.config.get("email_imap_server"))

    def get_unread(self, limit: int = 10) -> list:
        if not self.is_configured:
            return []

        server = self.config.get("email_imap_server", "")
        port = int(self.config.get("email_imap_port", 993))
        addr = self.config.get("email_address", "")
        pwd = self.config.get("email_app_password", "")

        try:
            mail = imaplib.IMAP4_SSL(server, port)
            mail.login(addr, pwd)
            mail.select("INBOX")

            _, data = mail.search(None, "UNSEEN")
            ids = data[0].split()
            if not ids:
                mail.logout()
                return []

            ids = ids[-limit:]
            emails = []

            for eid in reversed(ids):
                _, msg_data = mail.fetch(eid, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                subject = self._decode_header(msg.get("Subject", ""))
                sender = self._decode_header(msg.get("From", ""))
                date = msg.get("Date", "")
                body = self._get_body(msg)[:500]

                emails.append({
                    "id": eid.decode(),
                    "subject": subject,
                    "from": sender,
                    "date": date,
                    "body_preview": body,
                })

            mail.logout()
            return emails

        except imaplib.IMAP4.error as e:
            logger.error("[Email] IMAP error: %s", e)
            return []
        except Exception as e:
            logger.error("[Email] Error fetching mail: %s", e)
            return []

    def send_reply(self, to: str, subject: str, body: str) -> bool:
        if not self.is_configured:
            return False

        smtp_server = self.config.get("email_smtp_server", "")
        smtp_port = int(self.config.get("email_smtp_port", 587))
        addr = self.config.get("email_address", "")
        pwd = self.config.get("email_app_password", "")

        if not smtp_server:
            imap = self.config.get("email_imap_server", "")
            smtp_server = imap.replace("imap", "smtp")

        msg = MIMEMultipart()
        msg["From"] = addr
        msg["To"] = to
        msg["Subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"
        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(addr, pwd)
                server.send_message(msg)
            logger.info("[Email] Antwort gesendet an %s", to)
            return True
        except smtplib.SMTPAuthenticationError as e:
            logger.error("[Email] SMTP auth error: %s", e)
            return False
        except Exception as e:
            logger.error("[Email] sending failed: %s", e)
            return False

    def set_pending_draft(self, to: str, subject: str, body: str):
        self._pending_draft = {"to": to, "subject": subject, "body": body}

    def get_pending_draft(self) -> Optional[dict]:
        return self._pending_draft

    def send_pending_draft(self) -> bool:
        if not self._pending_draft:
            return False
        result = self.send_reply(
            self._pending_draft["to"],
            self._pending_draft["subject"],
            self._pending_draft["body"]
        )
        if result:
            self._pending_draft = None
        return result

    def discard_pending_draft(self):
        self._pending_draft = None

    def format_emails_text(self, emails: list) -> str:
        if not emails:
            return "No unread emails."
        lines = []
        for i, em in enumerate(emails, 1):
            lines.append(f"**{i}.** From: {em['from']}\n   Subject: {em['subject']}\n   {em['body_preview'][:100]}...")
        return "\n\n".join(lines)

    @staticmethod
    def _decode_header(value: str) -> str:
        if not value:
            return ""
        parts = decode_header(value)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(str(part))
        return " ".join(decoded)

    @staticmethod
    def _get_body(msg) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""

"""SMTP email delivery for account verification and password reset codes."""

import asyncio
import smtplib
from email.message import EmailMessage

from app.ports import DependencyUnavailable
from app.settings import Settings


class SMTPEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._username = settings.smtp_username
        self._password = settings.smtp_password
        self._from_email = settings.smtp_from_email
        self._starttls = settings.smtp_starttls

    async def send_code(self, recipient: str, purpose: str, code: str) -> None:
        if not self._host or not self._from_email:
            raise DependencyUnavailable("Email delivery is not configured.")
        subject = (
            "Verify your Nyaya-Mitra account"
            if purpose == "verify"
            else "Reset your Nyaya-Mitra password"
        )
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._from_email
        message["To"] = recipient
        message.set_content(
            f"Your Nyaya-Mitra verification code is {code}. "
            "It expires in 10 minutes. If you did not request this, ignore this email."
        )
        try:
            await asyncio.to_thread(self._send_sync, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise DependencyUnavailable("Email delivery is temporarily unavailable.") from exc

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=10) as client:
            if self._starttls:
                client.starttls()
            if self._username and self._password:
                client.login(self._username, self._password)
            client.send_message(message)

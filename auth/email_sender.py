from __future__ import annotations

import smtplib
from email.message import EmailMessage

from utils.config import Settings


class EmailDeliveryError(RuntimeError):
    pass


class EmailSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_verification_code(self, recipient: str, code: str) -> None:
        if not self.settings.smtp_host or not self.settings.email_from:
            raise EmailDeliveryError("Le service d'envoi d'e-mails n'est pas configuré")

        message = EmailMessage()
        message["Subject"] = f"{self.settings.app_name} - code de vérification"
        message["From"] = self.settings.email_from
        message["To"] = recipient
        message.set_content(
            "\n".join(
                [
                    "Bonjour,",
                    "",
                    f"Votre code de vérification est : {code}",
                    "",
                    f"Il expire dans {self.settings.email_verification_minutes} minutes.",
                    "Si vous n'avez pas demandé ce compte, ignorez cet e-mail.",
                ]
            )
        )

        try:
            if self.settings.smtp_use_ssl:
                server = smtplib.SMTP_SSL(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=15,
                )
            else:
                server = smtplib.SMTP(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=15,
                )
            with server:
                if self.settings.smtp_use_tls and not self.settings.smtp_use_ssl:
                    server.starttls()
                if self.settings.smtp_username:
                    server.login(self.settings.smtp_username, self.settings.smtp_password)
                server.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("Impossible d'envoyer l'e-mail de vérification") from exc

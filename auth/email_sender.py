from __future__ import annotations

import smtplib
import logging
from email.message import EmailMessage

from utils.config import Settings

LOGGER = logging.getLogger(__name__)


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
        except smtplib.SMTPAuthenticationError as exc:
            LOGGER.error("SMTP authentication rejected: code=%s error=%s", exc.smtp_code, exc.smtp_error)
            raise EmailDeliveryError("Le serveur e-mail a refusé les identifiants SMTP") from exc
        except smtplib.SMTPSenderRefused as exc:
            LOGGER.error("SMTP sender rejected: code=%s sender=%s error=%s", exc.smtp_code, exc.sender, exc.smtp_error)
            raise EmailDeliveryError("L'adresse d'expédition n'est pas autorisée par Brevo") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            LOGGER.error("SMTP recipient rejected: recipients=%s", list(exc.recipients))
            raise EmailDeliveryError("L'adresse du destinataire a été refusée par le service e-mail") from exc
        except smtplib.SMTPDataError as exc:
            LOGGER.error("SMTP message rejected: code=%s error=%s", exc.smtp_code, exc.smtp_error)
            raise EmailDeliveryError("Brevo a refusé le message ou le quota d'envoi est atteint") from exc
        except (OSError, smtplib.SMTPException) as exc:
            LOGGER.exception("SMTP delivery failed")
            raise EmailDeliveryError("Impossible d'envoyer l'e-mail de vérification") from exc

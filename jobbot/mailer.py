"""SMTP sending, used both for email applications and for notifications to you."""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from .config import Config


class MailError(RuntimeError):
    pass


def smtp_settings() -> dict:
    s = {
        "host": Config.env("SMTP_HOST"),
        "port": int(Config.env("SMTP_PORT", "587")),
        "user": Config.env("SMTP_USER"),
        "password": Config.env("SMTP_PASSWORD"),
        "from": Config.env("SMTP_FROM") or Config.env("SMTP_USER"),
    }
    return s


def smtp_configured() -> bool:
    s = smtp_settings()
    return bool(s["host"] and s["user"] and s["password"])


def send_email(to: list[str] | str, subject: str, text: str, *, html: str | None = None,
               attachments: list[Path] | None = None, cc: list[str] | None = None,
               reply_to: str | None = None, from_name: str | None = None) -> str:
    s = smtp_settings()
    if not smtp_configured():
        raise MailError("SMTP is not configured (SMTP_HOST, SMTP_USER, SMTP_PASSWORD in .env)")
    to = [to] if isinstance(to, str) else to
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, s["from"])) if from_name else s["from"]
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for path in attachments or []:
        path = Path(path)
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)

    context = ssl.create_default_context()
    try:
        if s["port"] == 465:
            with smtplib.SMTP_SSL(s["host"], s["port"], context=context, timeout=60) as server:
                server.login(s["user"], s["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(s["host"], s["port"], timeout=60) as server:
                server.starttls(context=context)
                server.login(s["user"], s["password"])
                server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"sending email failed: {exc}") from exc
    return msg["Message-ID"]

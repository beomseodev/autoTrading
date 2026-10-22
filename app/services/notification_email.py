import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from fastapi import HTTPException


def email_ready():
    return bool(os.getenv("GOOGLE_OWNER_EMAIL") and len(os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()) == 16)


def send_email(subject: str, body: str):
    if not email_ready():
        raise HTTPException(503, "알림 메일 발송을 위한 Gmail 앱 비밀번호 설정이 필요합니다.")
    owner = os.environ["GOOGLE_OWNER_EMAIL"]
    message = EmailMessage()
    message["From"] = owner
    message["To"] = owner
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15, context=ssl.create_default_context()) as smtp:
            smtp.login(owner, os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError):
        raise HTTPException(502, "알림 메일을 보내지 못했습니다. Gmail 설정을 확인해주세요.") from None

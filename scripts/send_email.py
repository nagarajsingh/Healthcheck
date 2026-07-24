#!/usr/bin/env python3

import argparse
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List


def split_addresses(value: str) -> List[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def required_env(*names: str) -> str:
    value = first_env(*names)
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {' or '.join(names)}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--html",
        "--html-file",
        dest="html",
        default="output/health-report.html",
        help="Path to the generated HTML health report",
    )
    parser.add_argument(
        "--subject-file",
        default="output/email-subject.txt",
        help="Path to the generated email subject file",
    )
    args = parser.parse_args()

    smtp_host = required_env("SMTP_HOST")
    smtp_port = int(first_env("SMTP_PORT", default="25"))
    smtp_username = first_env("SMTP_USERNAME")
    smtp_password = first_env("SMTP_PASSWORD")
    starttls = first_env("SMTP_STARTTLS", default="false").lower() == "true"
    sender = required_env("MAIL_FROM", "MASHREQ_EMAIL_FROM")
    recipients = split_addresses(required_env("MAIL_TO", "MASHREQ_EMAIL_TO"))
    bcc = split_addresses(first_env("MAIL_BCC", "MASHREQ_EMAIL_BCC"))

    html_path = Path(args.html)
    subject_path = Path(args.subject_file)

    if not html_path.is_file():
        raise FileNotFoundError(f"HTML report not found: {html_path}")
    if not subject_path.is_file():
        raise FileNotFoundError(f"Email subject file not found: {subject_path}")

    html = html_path.read_text(encoding="utf-8")
    subject = subject_path.read_text(encoding="utf-8").strip()

    if not subject:
        raise RuntimeError(f"Email subject file is empty: {subject_path}")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = subject
    message.set_content("The Kubernetes health report is available in the HTML part of this email.")
    message.add_alternative(html, subtype="html")

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        if starttls:
            smtp.starttls()
            smtp.ehlo()
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message, to_addrs=recipients + bcc)

    print(f"Email sent to {', '.join(recipients)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

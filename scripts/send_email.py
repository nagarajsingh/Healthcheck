#!/usr/bin/env python3

import argparse
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List


def split_addresses(value: str) -> List[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


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

    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.getenv("SMTP_PORT", "25"))
    smtp_username = os.getenv("SMTP_USERNAME", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    starttls = os.getenv("SMTP_STARTTLS", "false").lower() == "true"
    sender = os.environ["MAIL_FROM"]
    recipients = split_addresses(os.environ["MAIL_TO"])
    bcc = split_addresses(os.getenv("MAIL_BCC", ""))

    if not recipients:
        raise RuntimeError("MAIL_TO does not contain any recipient")

    html = Path(args.html).read_text(encoding="utf-8")
    subject = Path(args.subject_file).read_text(encoding="utf-8").strip()

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

import os
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

# ✅ Load .env from project root
load_dotenv()

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
PORTAL_URL = os.getenv("PORTAL_URL")
default_password = os.getenv("DEFAULT_PASSWORD")

# 🔍 HARD DEBUG (TEMP)
print("🔍 SMTP_SERVER:", SMTP_SERVER)
print("🔍 SMTP_PORT:", SMTP_PORT)
print("🔍 EMAIL_FROM:", EMAIL_FROM)
print("🔍 EMAIL_PASSWORD LOADED:", bool(EMAIL_PASSWORD))


def send_exam_assignment_email(to_email: str, exam_title: str, send_password=False):
    print("📧 send_exam_assignment_email() CALLED")

    msg = EmailMessage()
    msg["Subject"] = "NMK Certification Exam Assigned"
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email

    if send_password:
        password_section = f"\nTemporary Password: {default_password}\n"
    else:
        password_section = "\nPlease use your existing password to login.\n"

    msg.set_content(f"""
        Hello,

        You have been assigned a new exam.

        📘 Exam Title: {exam_title}

        Email: {to_email}
        {password_section}

        🔗 Portal:
        {PORTAL_URL}

        Regards,
        NMK Certification Team
        """
    )
    try:
        print("📡 Connecting to Gmail SMTP...")
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.set_debuglevel(1)   # 🔥 VERY IMPORTANT
            server.starttls()
            print("🔐 Logging in to Gmail...")
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            print("✉️ Sending email...")
            server.send_message(msg)
            print("✅ EMAIL SENT SUCCESSFULLY")
    except Exception as e:
        print("❌ SMTP ERROR:", e)
        raise

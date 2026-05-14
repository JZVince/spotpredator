#!/usr/bin/env python3
"""Test email and SMS sending"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime

# Load .env
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip()

email_address = os.getenv("EMAIL_ADDRESS", "")
email_password = os.getenv("EMAIL_PASSWORD", "")
sms_gateway = os.getenv("SMS_GATEWAY", "")

print(f"Email: {email_address}")
print(f"Password configured: {'yes' if email_password else 'no'}")
print(f"SMS gateway: {sms_gateway}")

# Test SMS
try:
    sms_msg = MIMEText("SpotPredator test message - SMS gateway working!", 'plain', 'utf-8')
    sms_msg['From'] = email_address
    sms_msg['To'] = sms_gateway
    sms_msg['Subject'] = ""

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(email_address, email_password)
        server.send_message(sms_msg)

    print("SMS sent successfully!")

except Exception as e:
    print(f"SMS failed: {e}")

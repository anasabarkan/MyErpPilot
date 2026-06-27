import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)

def send_low_stock_email(to_email: str, product_name: str, qty_available: float, min_qty: float, platform_link: str) -> None:
    if not settings.ENABLE_EMAIL_NOTIFICATIONS:
        logger.debug(f"Email notifications disabled. Skipping email for {product_name} to {to_email}")
        return

    is_out_of_stock = qty_available <= 0
    subject_prefix = "[URGENT] Out of Stock:" if is_out_of_stock else "[ALERT] Low Stock:"
    subject = f"{subject_prefix} {product_name}"
    
    body = f"""
Hello,

This is an automated alert regarding inventory levels.

Product: {product_name}
Current Quantity: {qty_available}
Minimum Threshold: {min_qty}

An approval request has been generated. Please review and decide whether to create a purchase order:
{platform_link}

Best regards,
My ERP Pilot System
"""

    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to_email

    try:
        if settings.SMTP_USE_TLS:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
                
        logger.info(f"Successfully sent low stock email for '{product_name}' to {to_email}")
    except Exception as e:
        # We don't want to block the workflow if email fails
        logger.error(f"Failed to send email to {to_email} for '{product_name}': {e}")

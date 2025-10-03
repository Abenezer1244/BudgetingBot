import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

def send_email_with_pdf(to_email: str, subject: str, html_content: str, pdf_bytes: bytes, filename: str="weekly_report.pdf"):
    api_key = os.getenv("SENDGRID_API_KEY","").strip()
    if not api_key:
        raise RuntimeError("Missing SENDGRID_API_KEY")
    message = Mail(
        from_email="no-reply@budgetbot.local",
        to_emails=to_email,
        subject=subject,
        html_content=html_content,
    )
    import base64
    encoded = base64.b64encode(pdf_bytes).decode()
    attachment = Attachment(
        FileContent(encoded),
        FileName(filename),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    message.attachment = attachment
    sg = SendGridAPIClient(api_key)
    sg.send(message)

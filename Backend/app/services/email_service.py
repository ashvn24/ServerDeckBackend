import smtplib
from email.message import EmailMessage
from string import Template
import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.config import get_settings

# Simple thread pool to handle synchronous smtplib calls without blocking the async event loop
executor = ThreadPoolExecutor(max_workers=5)


def send_email_sync(to_email: str, subject: str, html_content: str):
    """
    Synchronous function to send email via SMTP.
    Runs inside a thread pool.
    """
    settings = get_settings()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to_email
    
    # Set the content to HTML
    msg.set_content(html_content, subtype="html")

    try:
        # MailerSend supports STARTTLS on port 587
        server = smtplib.SMTP(settings.smtp_server, settings.smtp_port)
        server.starttls()
        server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)
        server.quit()
        print(f"Email sent successfully to {to_email}")
    except Exception as e:
        print(f"Failed to send email to {to_email}: {str(e)}")
        # We catch exceptions so that email failure doesn't break the main flow.
        # In a real app, you'd log this properly and potentially retry.


async def send_email_async(to_email: str, subject: str, html_content: str):
    """
    Asynchronously sends an email by offloading to a thread pool.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(executor, send_email_sync, to_email, subject, html_content)


# Premium HTML Email Template
# This uses standard CSS for modern aesthetics (glassmorphism/gradients/clean typography)
BASE_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: #f3f4f6;
            margin: 0;
            padding: 0;
            color: #1f2937;
            -webkit-font-smoothing: antialiased;
        }
        .wrapper {
            width: 100%;
            background-color: #f3f4f6;
            padding: 40px 0;
        }
        .container {
            max-width: 580px;
            margin: 0 auto;
            background-color: #ffffff;
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
        }
        .header {
            background: linear-gradient(135deg, #111827 0%, #374151 100%);
            padding: 40px 32px;
            text-align: center;
        }
        .header h1 {
            color: #ffffff;
            margin: 0;
            font-size: 28px;
            font-weight: 800;
            letter-spacing: -0.05em;
            text-transform: uppercase;
        }
        .header h1 span {
            color: #6366f1;
        }
        .content {
            padding: 48px 40px;
            line-height: 1.7;
        }
        .content h2 {
            margin-top: 0;
            color: #111827;
            font-size: 22px;
            font-weight: 700;
            letter-spacing: -0.025em;
            margin-bottom: 24px;
        }
        .content p {
            margin-bottom: 24px;
            color: #4b5563;
            font-size: 16px;
        }
        .btn-wrapper {
            text-align: center;
            margin-top: 40px;
            margin-bottom: 24px;
        }
        .btn {
            display: inline-block;
            background: linear-gradient(to right, #4f46e5, #6366f1);
            color: #ffffff !important;
            text-decoration: none;
            padding: 14px 32px;
            border-radius: 9999px; /* Pill shape */
            font-weight: 600;
            font-size: 16px;
            box-shadow: 0 4px 14px 0 rgba(79, 70, 229, 0.39);
            text-align: center;
        }
        .footer {
            background-color: #f9fafb;
            padding: 32px 40px;
            text-align: center;
            font-size: 13px;
            color: #9ca3af;
            border-top: 1px solid #f3f4f6;
        }
        .footer a {
            color: #6b7280;
            text-decoration: none;
            margin: 0 8px;
        }
        .highlight {
            font-weight: 600;
            color: #4f46e5;
        }
        .divider {
            height: 1px;
            background-color: #e5e7eb;
            margin: 32px 0;
        }
        @media only screen and (max-width: 600px) {
            .container {
                border-radius: 0;
                margin: 0;
                width: 100%;
            }
            .wrapper {
                padding: 0;
            }
            .content {
                padding: 32px 24px;
            }
        }
    </style>
</head>
<body>
    <div class="wrapper">
        <div class="container">
            <div class="header">
                <h1>Server<span>Deck</span></h1>
            </div>
            <div class="content">
                $body_content
            </div>
            <div class="footer">
                <p>&copy; 2026 ServerDeck. All rights reserved.</p>
                <div style="margin-top: 12px;">
                    <a href="#">Privacy Policy</a> &bull; <a href="#">Terms of Service</a> &bull; <a href="#">Support</a>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""


async def send_org_creation_email(to_email: str, org_name: str, admin_name: str):
    """
    Sends a welcome email after an organization is successfully registered.
    """
    settings = get_settings()
    subject = f"Welcome to ServerDeck, {org_name}!"
    
    body = f"""
        <h2>Welcome aboard, {admin_name}!</h2>
        <p>Your organization <span class="highlight">{org_name}</span> has been successfully created on ServerDeck.</p>
        <p>You can now log in to your dashboard to start managing your servers, teams, and infrastructure with ease.</p>
        <div class="btn-wrapper">
            <a href="{settings.ui_base_url}/login" class="btn">Go to Dashboard</a>
        </div>
    """
    
    html_content = Template(BASE_HTML_TEMPLATE).substitute(body_content=body)
    await send_email_async(to_email, subject, html_content)


async def send_invitation_email(to_email: str, inviter_name: str, invite_link: str, org_name: str):
    """
    Sends an invitation email for a user to join an organization.
    """
    subject = f"You've been invited to join {org_name} on ServerDeck"
    
    body = f"""
        <h2>You're invited!</h2>
        <p>You have been invited to join <span class="highlight">{org_name}</span> on ServerDeck.</p>
        <p>Click the button below to accept your invitation, set up your account, and access the platform.</p>
        <div class="btn-wrapper">
            <a href="{invite_link}" class="btn">Accept Invitation</a>
        </div>
        <p style="font-size: 13px; color: #9ca3af; text-align: center;">If you didn't expect this invitation, you can safely ignore this email.</p>
    """
    
    html_content = Template(BASE_HTML_TEMPLATE).substitute(body_content=body)
    await send_email_async(to_email, subject, html_content)


async def send_access_approved_email(to_email: str, user_name: str):
    """
    Sends a welcome/access-approved email after a waitlist request is approved.
    """
    settings = get_settings()
    subject = "Your ServerDeck access request has been approved!"
    
    body = f"""
        <h2>Welcome to ServerDeck, {user_name}!</h2>
        <p>Your request for access has been approved by the platform owner.</p>
        <p>You can now log in using the password you chose during signup.</p>
        <div class="btn-wrapper">
            <a href="{settings.ui_base_url}/login" class="btn">Log In</a>
        </div>
    """
    
    html_content = Template(BASE_HTML_TEMPLATE).substitute(body_content=body)
    await send_email_async(to_email, subject, html_content)


async def send_password_reset_email(to_email: str, name: str, reset_link: str):
    """
    Sends a password reset email containing a secure link.
    """
    subject = "Reset your ServerDeck password"
    
    body = f"""
        <h2>Password Reset Request</h2>
        <p>Hello {name},</p>
        <p>We received a request to reset the password associated with your ServerDeck account.</p>
        <p>Click the button below to choose a new password. This link will expire in 1 hour.</p>
        <div class="btn-wrapper">
            <a href="{reset_link}" class="btn">Reset Password</a>
        </div>
        <p style="font-size: 13px; color: #9ca3af; text-align: center; margin-top: 20px;">
            If you did not request a password reset, you can safely ignore this email.
        </p>
    """
    
    html_content = Template(BASE_HTML_TEMPLATE).substitute(body_content=body)
    await send_email_async(to_email, subject, html_content)


async def send_otp_email(to_email: str, name: str, code: str):
    """
    Sends a two-factor verification OTP code email.
    """
    subject = f"{code} is your ServerDeck verification code"
    
    body = f"""
        <h2>Two-Factor Verification</h2>
        <p>Hello {name},</p>
        <p>A request was made to access your ServerDeck account.</p>
        <p>Please use the following 6-digit one-time code to complete your log in. This code is valid for 5 minutes.</p>
        <div class="btn-wrapper" style="text-align: center; margin: 30px 0;">
            <span style="font-family: monospace; font-size: 32px; font-weight: 900; letter-spacing: 6px; color: #8b5cf6; background: rgba(139, 92, 246, 0.05); padding: 12px 24px; border-radius: 12px; border: 1px solid rgba(139, 92, 246, 0.2); display: inline-block;">
                {code}
            </span>
        </div>
        <p style="font-size: 13px; color: #9ca3af; text-align: center; margin-top: 20px;">
            If you did not request this login, please change your password immediately.
        </p>
    """
    
    html_content = Template(BASE_HTML_TEMPLATE).substitute(body_content=body)
    await send_email_async(to_email, subject, html_content)


async def send_access_request_alert_email(
    requester_email: str,
    name: str | None = None,
    request_type: str | None = None,
    org_name: str | None = None,
):
    """
    Sends an alert email to ashwinvk77@gmail.com when a new waitlist/access request is created.
    """
    subject = f"Alert: New ServerDeck Access Request from {requester_email}"
    
    # Format details nicely for the template
    name_str = name if name else "Not specified"
    request_type_str = request_type if request_type else "Not specified"
    org_name_str = org_name if org_name else "Not specified"
    
    body = f"""
        <h2>New Access Request Received</h2>
        <p>A new user has requested access on the ServerDeck landing page.</p>
        <p><strong>Details:</strong></p>
        <ul>
            <li><strong>Email:</strong> {requester_email}</li>
            <li><strong>Name:</strong> {name_str}</li>
            <li><strong>Request Type:</strong> {request_type_str}</li>
            <li><strong>Organization:</strong> {org_name_str}</li>
        </ul>
        <p>You can approve or reject this request in the platform admin panel.</p>
    """
    
    html_content = Template(BASE_HTML_TEMPLATE).substitute(body_content=body)
    await send_email_async("ashwinvk77@gmail.com", subject, html_content)


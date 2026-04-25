#!/usr/bin/env python3
"""
Team Kenya (NOCK) — Viral Loops Integration Server
===================================================
FastAPI webhook server that bridges Tally form submissions
to Viral Loops referral campaign participant registration.

Flow:
1. User fills Tally form (with optional ?ref= referral code in URL)
2. Tally fires webhook on submission → hits this server
3. Server extracts user data (name, email, location, preferences)
4. Server calls Viral Loops API to register participant
5. Server sends confirmation email with unique referral link
6. Server logs everything for analytics

Author: ReplicaDX on behalf of Team Kenya (NOCK)
"""

import os
import json
import hmac
import hashlib
import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ─── Configuration ───────────────────────────────────────────────
VL_PUBLIC_TOKEN = os.getenv("VL_PUBLIC_TOKEN", "")
VL_API_TOKEN = os.getenv("VL_API_TOKEN", "")
VL_CAMPAIGN_ID = os.getenv("VL_CAMPAIGN_ID", "")
TALLY_SIGNING_SECRET = os.getenv("TALLY_SIGNING_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

VL_API_BASE = "https://app.viral-loops.com/api/v3"

# ─── Logging ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("integration.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("team_kenya_vl")

# ─── App Setup ───────────────────────────────────────────────────
app = FastAPI(
    title="Team Kenya — Viral Loops Integration",
    description="Webhook bridge between Tally forms and Viral Loops referral campaigns",
    version="1.0.0"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ─── In-memory store (replace with DB in production) ─────────────
participants_store = {}


# ─── Helpers ─────────────────────────────────────────────────────

def verify_tally_signature(payload: bytes, signature: str) -> bool:
    """Verify Tally webhook signature using HMAC-SHA256."""
    if not TALLY_SIGNING_SECRET:
        logger.warning("No Tally signing secret configured — skipping verification")
        return True
    calculated = hmac.new(
        TALLY_SIGNING_SECRET.encode(),
        payload,
        hashlib.sha256
    ).digest()
    import base64
    calculated_b64 = base64.b64encode(calculated).decode()
    return hmac.compare_digest(calculated_b64, signature)


def extract_tally_fields(data: dict) -> dict:
    """
    Extract structured fields from Tally webhook payload.
    Maps Tally field labels to our internal field names.
    """
    fields = data.get("data", {}).get("fields", [])
    extracted = {}

    # Map Tally labels to our keys
    label_map = {
        "Full Name": "full_name",
        "Email Address": "email",
        "WhatsApp Number": "whatsapp",
        "Where are you currently based?": "location",
        "Which 2KM Collection item interests you most?": "collection_interest",
        "How much would you spend on official Team Kenya merchandise?": "price_tier",
        "What other Team Kenya merchandise would you buy?": "other_merch",
        "What's your biggest concern when ordering from Kenya?": "shipping_concern",
        "Which features matter most to you in the Team Kenya platform?": "platform_features",
        "How would you most like to support Team Kenya athletes?": "athlete_support",
        "Which Team Kenya sports do you follow?": "sports_followed",
    }

    for field in fields:
        label = field.get("label", "")
        value = field.get("value", "")

        # Check direct label match
        if label in label_map:
            extracted[label_map[label]] = value
        # Check partial match for flexibility
        else:
            for tally_label, key in label_map.items():
                if tally_label.lower() in label.lower():
                    extracted[key] = value
                    break

    # Also extract hidden fields (referral code from URL params)
    for field in fields:
        if field.get("type") == "HIDDEN_FIELDS":
            if field.get("label", "").lower() in ("ref", "referral", "referrer", "referral_code"):
                extracted["referrer_code"] = field.get("value", "")

    return extracted


async def register_viral_loops_participant(
    email: str,
    first_name: str,
    referrer_code: Optional[str] = None,
    extra_data: Optional[dict] = None
) -> dict:
    """
    Register a new participant in Viral Loops campaign.
    Returns the participant's referral code and status.
    """
    payload = {
        "publicToken": VL_PUBLIC_TOKEN,
        "user": {
            "firstname": first_name,
            "email": email,
        }
    }

    # Add extra data (location, preferences, etc.)
    if extra_data:
        payload["user"]["extraData"] = extra_data

    # Add referrer if this user was referred
    if referrer_code:
        payload["referrer"] = {
            "referralCode": referrer_code
        }

    logger.info(f"Registering participant: {email} (referrer: {referrer_code or 'none'})")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{VL_API_BASE}/campaign/participant",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            timeout=10.0
        )

    if response.status_code == 200:
        result = response.json()
        logger.info(f"✅ Registered {email} → referralCode: {result.get('referralCode')}")
        return {
            "success": True,
            "referral_code": result.get("referralCode", ""),
            "is_new": result.get("isNew", True),
            "raw": result
        }
    else:
        logger.error(f"❌ VL registration failed for {email}: {response.status_code} — {response.text}")
        return {
            "success": False,
            "error": response.text,
            "status_code": response.status_code
        }


async def get_participant_data(email: str) -> dict:
    """Fetch participant data from Viral Loops (referral count, rewards, rank)."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{VL_API_BASE}/campaign/participant/data",
            params={
                "publicToken": VL_PUBLIC_TOKEN,
                "email": email
            },
            headers={
                "Accept": "application/json"
            },
            timeout=10.0
        )

    if response.status_code == 200:
        return response.json()
    else:
        logger.error(f"Failed to get data for {email}: {response.status_code}")
        return {}


async def send_referral_email(email: str, name: str, referral_code: str):
    """Send the participant their unique referral link via email."""
    referral_link = f"{BASE_URL}/refer/{referral_code}"

    # Build email content
    subject = "🏃 Your Team Kenya Referral Link — Start Earning Rewards!"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: #000; color: #fff; padding: 30px; text-align: center; border-radius: 8px 8px 0 0;">
            <h1 style="margin: 0; color: #C8102E;">🇰🇪 Team Kenya</h1>
            <p style="margin: 10px 0 0; color: #FFD700;">2KM Collection — Referral Programme</p>
        </div>

        <div style="background: #f9f9f9; padding: 30px; border-radius: 0 0 8px 8px;">
            <h2>Welcome, {name}! 🎉</h2>

            <p>You're now on the Team Kenya waitlist. Share your unique referral link to unlock exclusive rewards:</p>

            <div style="background: #fff; border: 2px solid #C8102E; border-radius: 8px; padding: 20px; text-align: center; margin: 20px 0;">
                <p style="font-size: 12px; color: #666; margin: 0 0 8px;">YOUR REFERRAL LINK</p>
                <a href="{referral_link}" style="font-size: 18px; color: #C8102E; font-weight: bold; text-decoration: none;">
                    {referral_link}
                </a>
            </div>

            <h3 style="color: #000;">🏆 Reward Tiers</h3>
            <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
                <tr style="background: #C8102E; color: #fff;">
                    <th style="padding: 10px; text-align: left;">Tier</th>
                    <th style="padding: 10px; text-align: left;">Referrals</th>
                    <th style="padding: 10px; text-align: left;">Reward</th>
                </tr>
                <tr style="background: #fff;">
                    <td style="padding: 10px; border-bottom: 1px solid #eee;">🏃 The Trailblazer</td>
                    <td style="padding: 10px; border-bottom: 1px solid #eee;">5</td>
                    <td style="padding: 10px; border-bottom: 1px solid #eee;">Early Access + 10% Discount</td>
                </tr>
                <tr style="background: #f9f9f9;">
                    <td style="padding: 10px; border-bottom: 1px solid #eee;">🏅 The Captain</td>
                    <td style="padding: 10px; border-bottom: 1px solid #eee;">10</td>
                    <td style="padding: 10px; border-bottom: 1px solid #eee;">VIP Access + Exclusive Merch</td>
                </tr>
                <tr style="background: #fff;">
                    <td style="padding: 10px;">🌍 Diaspora Champion</td>
                    <td style="padding: 10px;">15</td>
                    <td style="padding: 10px;">Free International Shipping + Badge</td>
                </tr>
            </table>

            <div style="text-align: center; margin: 25px 0;">
                <a href="{referral_link}" style="background: #C8102E; color: #fff; padding: 14px 40px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 16px;">
                    Share Your Link Now
                </a>
            </div>

            <p style="font-size: 13px; color: #888; text-align: center;">
                The more friends you refer, the bigger the rewards.<br>
                Track your progress anytime at <a href="{BASE_URL}/dashboard?email={email}">{BASE_URL}/dashboard</a>
            </p>
        </div>
    </div>
    """

    # Log the email (actual SMTP sending configured in production)
    logger.info(f"📧 Referral email queued for {email} — link: {referral_link}")

    # Try SMTP if configured
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")

    if smtp_host and smtp_user and smtp_password:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"Team Kenya <{smtp_user}>"
            msg["To"] = email
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(smtp_host, int(os.getenv("SMTP_PORT", 587))) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, email, msg.as_string())

            logger.info(f"✅ Email sent to {email}")
        except Exception as e:
            logger.error(f"❌ Email failed for {email}: {e}")
    else:
        logger.info("⚠️ SMTP not configured — email logged but not sent")

    return {"subject": subject, "referral_link": referral_link}


# ─── API Routes ──────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Landing page / health check."""
    return templates.TemplateResponse(request=request, name="index.html", context={
        "base_url": BASE_URL
    })


@app.post("/webhook/tally")
async def tally_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Main webhook endpoint — receives Tally form submissions.
    
    Flow:
    1. Verify signature (if signing secret configured)
    2. Extract user data from Tally payload
    3. Register participant in Viral Loops
    4. Queue confirmation email with referral link
    5. Return success
    """
    # Read raw body for signature verification
    body = await request.body()

    # Verify Tally signature
    signature = request.headers.get("Tally-Signature", "")
    if TALLY_SIGNING_SECRET and not verify_tally_signature(body, signature):
        logger.warning("Invalid Tally webhook signature!")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(f"📥 Tally webhook received: eventType={payload.get('eventType')}")

    # Only process form responses
    if payload.get("eventType") != "FORM_RESPONSE":
        return JSONResponse({"status": "ignored", "reason": "not a form response"})

    # Extract fields
    fields = extract_tally_fields(payload)
    logger.info(f"Extracted fields: {json.dumps(fields, indent=2)}")

    email = fields.get("email", "")
    full_name = fields.get("full_name", "")
    if not email:
        logger.error("No email found in submission!")
        return JSONResponse({"status": "error", "reason": "no email"}, status_code=400)

    # Parse first name
    first_name = full_name.split()[0] if full_name else "Friend"

    # Build extra data for Viral Loops custom fields
    extra_data = {}
    for key in ["location", "collection_interest", "price_tier", "other_merch",
                 "shipping_concern", "platform_features", "athlete_support",
                 "sports_followed", "whatsapp"]:
        if key in fields and fields[key]:
            extra_data[key] = str(fields[key]) if not isinstance(fields[key], str) else fields[key]

    # Get referrer code (from hidden field or URL param)
    referrer_code = fields.get("referrer_code", None)

    # Register in Viral Loops
    vl_result = await register_viral_loops_participant(
        email=email,
        first_name=first_name,
        referrer_code=referrer_code,
        extra_data=extra_data
    )

    # Store locally
    participants_store[email] = {
        "name": full_name,
        "email": email,
        "location": fields.get("location", "Unknown"),
        "referral_code": vl_result.get("referral_code", ""),
        "registered_at": datetime.utcnow().isoformat(),
        "tally_fields": fields,
        "vl_result": vl_result
    }

    # Send referral email in background
    if vl_result.get("success") and vl_result.get("referral_code"):
        background_tasks.add_task(
            send_referral_email,
            email=email,
            name=first_name,
            referral_code=vl_result["referral_code"]
        )

    return JSONResponse({
        "status": "success",
        "participant": {
            "email": email,
            "name": full_name,
            "referral_code": vl_result.get("referral_code", ""),
            "is_new": vl_result.get("is_new", True)
        }
    })


@app.get("/refer/{referral_code}")
async def referral_redirect(referral_code: str):
    """
    Referral link handler — redirects to Tally form with referral code.
    The ref code is passed as a hidden field parameter.
    """
    # Redirect to Tally form with referral code as URL parameter
    tally_form_url = f"https://tally.so/r/eqN5PQ?ref={referral_code}"
    logger.info(f"🔗 Referral redirect: code={referral_code} → {tally_form_url}")
    return RedirectResponse(url=tally_form_url, status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
async def participant_dashboard(request: Request, email: str = ""):
    """
    Participant dashboard — shows referral progress, link, and tier status.
    """
    participant_data = {}
    referral_code = ""
    referral_count = 0
    current_tier = "No tier yet"
    next_tier = "The Trailblazer (5 referrals)"
    referrals_needed = 5

    if email:
        # Try to get from Viral Loops
        vl_data = await get_participant_data(email)
        if vl_data:
            participant_data = vl_data.get("data", vl_data)
            referral_count = participant_data.get("referralCountTotal", participant_data.get("referralCount", 0))
            referral_code = participant_data.get("referralCode", "")

            # Calculate tier
            if referral_count >= 15:
                current_tier = "🌍 The Diaspora Champion"
                next_tier = "Maximum tier reached!"
                referrals_needed = 0
            elif referral_count >= 10:
                current_tier = "🏅 The Captain"
                next_tier = "The Diaspora Champion (15 referrals)"
                referrals_needed = 15 - referral_count
            elif referral_count >= 5:
                current_tier = "🏃 The Trailblazer"
                next_tier = "The Captain (10 referrals)"
                referrals_needed = 10 - referral_count
            else:
                referrals_needed = 5 - referral_count

        # Fallback to local store
        if not referral_code and email in participants_store:
            local = participants_store[email]
            referral_code = local.get("referral_code", "")

    referral_link = f"{BASE_URL}/refer/{referral_code}" if referral_code else ""

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "email": email,
        "referral_code": referral_code,
        "referral_link": referral_link,
        "referral_count": referral_count,
        "current_tier": current_tier,
        "next_tier": next_tier,
        "referrals_needed": referrals_needed,
        "base_url": BASE_URL
    })


@app.get("/api/participant/{email}")
async def api_participant(email: str):
    """API endpoint to get participant data (for AJAX calls)."""
    vl_data = await get_participant_data(email)
    local_data = participants_store.get(email, {})
    return JSONResponse({
        "viral_loops": vl_data,
        "local": local_data
    })


@app.get("/api/stats")
async def api_stats():
    """Basic campaign stats."""
    return JSONResponse({
        "total_local_participants": len(participants_store),
        "participants": [
            {
                "email": p["email"],
                "name": p["name"],
                "location": p["location"],
                "referral_code": p["referral_code"],
                "registered_at": p["registered_at"]
            }
            for p in participants_store.values()
        ]
    })


@app.get("/thankyou", response_class=HTMLResponse)
async def thank_you_page(request: Request):
    """Custom Thank You page shown after Tally form submission via redirect."""
    return templates.TemplateResponse(request=request, name="thankyou.html", context={
        "base_url": BASE_URL
    })


@app.get("/health")
async def health():
    """Health check endpoint."""
    return JSONResponse({
        "status": "healthy",
        "service": "Team Kenya Viral Loops Integration",
        "version": "1.0.0",
        "vl_configured": bool(VL_PUBLIC_TOKEN),
        "tally_signing_configured": bool(TALLY_SIGNING_SECRET),
        "timestamp": datetime.utcnow().isoformat()
    })


# ─── Run ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        reload=True
    )

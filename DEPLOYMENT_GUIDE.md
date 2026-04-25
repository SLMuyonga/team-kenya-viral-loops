# Team Kenya — Viral Loops Integration: Deployment Guide

**Version:** 1.0.0
**Author:** ReplicaDX on behalf of Team Kenya (NOCK)
**Date:** 25 April 2026

---

## Architecture Overview

This integration connects the Tally waitlist form to a Viral Loops milestone referral campaign via a lightweight FastAPI webhook server.

```
User clicks referral link
        │
        ▼
┌─────────────────────┐
│  /refer/{code}       │  ← Webhook Server
│  Redirects to Tally  │
│  with ?ref=CODE      │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Tally Form          │  ← tally.so/r/eqN5PQ
│  (captures ref code  │
│   as hidden field)   │
└─────────┬───────────┘
          │ Webhook fires on submit
          ▼
┌─────────────────────┐
│  /webhook/tally      │  ← Webhook Server
│  1. Verify signature │
│  2. Extract fields   │
│  3. Register in VL   │
│  4. Send email       │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Viral Loops API     │  ← app.viral-loops.com
│  POST /participant   │
│  Returns: refCode    │
└─────────────────────┘
          │
          ▼
┌─────────────────────┐
│  Confirmation Email  │
│  with referral link  │
│  + Dashboard link    │
└─────────────────────┘
```

---

## Step 1: Create Viral Loops Account & Campaign

1. Go to [https://viral-loops.com](https://viral-loops.com) and click **"Try for free"**
2. Sign up with **info@replicadx.net**
3. Create a new campaign → Select **"The Milestone Referral"** template
4. Name it: **"Team Kenya 2KM Collection — Referral Programme"**

### Configure Milestones

In the campaign editor, set up 3 milestones:

| Milestone | Referrals Required | Reward Name | Reward Description |
|---|---|---|---|
| 1 | 5 | The Trailblazer | Early access to 2KM Collection + 10% launch discount |
| 2 | 10 | The Captain | VIP access to limited drops + exclusive Team Kenya merch item |
| 3 | 15 | The Diaspora Champion | Free international shipping on first order + Diaspora Member Badge |

### Get API Credentials

Go to **Campaign → Installation** and copy:
- **Public Token** (publicToken)
- **Secret API Token** (apiToken)
- **Campaign ID** (campaignId)

---

## Step 2: Configure Environment

```bash
cd /home/ubuntu/viral_loops_integration
cp .env.example .env
```

Edit `.env` with your actual values:

```env
VL_PUBLIC_TOKEN=your_public_token_from_step_1
VL_API_TOKEN=your_secret_api_token_from_step_1
VL_CAMPAIGN_ID=your_campaign_id_from_step_1

TALLY_SIGNING_SECRET=your_tally_signing_secret
TALLY_API_KEY=tly-NAE1FQA1EzwErBBOdHqafgaIAkyhq3YI

HOST=0.0.0.0
PORT=8000
BASE_URL=https://your-deployment-domain.com

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=info@replicadx.net
SMTP_PASSWORD=your_app_password
```

---

## Step 3: Run Configuration Script

This verifies your API keys work and tests participant registration:

```bash
cd /home/ubuntu/viral_loops_integration/scripts
python3 configure_campaign.py \
  --public-token YOUR_PUBLIC_TOKEN \
  --api-token YOUR_API_TOKEN \
  --campaign-id YOUR_CAMPAIGN_ID
```

Expected output:
```
✅ Campaign accessible!
✅ Test participant registered!
✅ .env file written!
```

---

## Step 4: Add Hidden Field to Tally Form

The referral tracking requires a hidden field named `ref` in the Tally form.

### Option A: Via Script
```bash
python3 scripts/setup_tally_hidden_field.py
```

### Option B: Via Tally Dashboard
1. Open the form editor at [tally.so](https://tally.so)
2. Add a new block → **Hidden Fields**
3. Field name: `ref`
4. Prefill: **URL parameter**
5. URL parameter name: `ref`
6. Save and publish

---

## Step 5: Configure Tally Webhook

1. Go to your Tally form → **Integrations** tab
2. Click **Connect** next to Webhooks
3. Set the endpoint URL to: `https://your-domain.com/webhook/tally`
4. (Optional) Add a signing secret and copy it to your `.env` as `TALLY_SIGNING_SECRET`
5. Save

---

## Step 6: Deploy the Server

### Option A: Direct (VPS/Cloud VM)

```bash
cd /home/ubuntu/viral_loops_integration
pip install -r requirements.txt
python3 main.py
```

The server runs on port 8000. Use nginx or Caddy as a reverse proxy with HTTPS.

### Option B: Docker

```bash
cd /home/ubuntu/viral_loops_integration
docker build -t team-kenya-vl .
docker run -d --name team-kenya-vl \
  --env-file .env \
  -p 8000:8000 \
  team-kenya-vl
```

### Option C: Railway / Render / Fly.io

1. Push the `viral_loops_integration` directory to a Git repo
2. Connect the repo to Railway/Render/Fly.io
3. Set environment variables from `.env`
4. Deploy — the `Dockerfile` handles everything

---

## Step 7: Verify End-to-End Flow

### Test 1: Health Check
```bash
curl https://your-domain.com/health
```
Expected: `{"status": "healthy", "vl_configured": true, ...}`

### Test 2: Referral Link Redirect
Visit: `https://your-domain.com/refer/TEST123`
Should redirect to: `https://tally.so/r/eqN5PQ?ref=TEST123`

### Test 3: Full Form Submission
1. Open `https://your-domain.com/refer/TEST123`
2. Fill out the Tally form
3. Check server logs for webhook receipt
4. Check Viral Loops dashboard for new participant
5. Check email inbox for referral link

### Test 4: Dashboard
Visit: `https://your-domain.com/dashboard?email=your@email.com`
Should show referral count, tier progress, and share buttons.

---

## API Endpoints Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Landing page with programme info |
| `/webhook/tally` | POST | Tally webhook receiver (main integration) |
| `/refer/{code}` | GET | Referral link → redirects to Tally with ref code |
| `/dashboard` | GET | Participant referral dashboard |
| `/api/participant/{email}` | GET | Get participant data (JSON) |
| `/api/stats` | GET | Campaign statistics (JSON) |
| `/health` | GET | Health check |

---

## Referral Link Format

Each participant gets a unique referral code from Viral Loops. Their shareable link is:

```
https://your-domain.com/refer/{referralCode}
```

This redirects to the Tally form with the referral code as a URL parameter, which is captured by the hidden field and sent back in the webhook payload.

---

## Monitoring & Logs

- Server logs: `integration.log` in the project directory
- Viral Loops dashboard: Real-time participant and referral tracking
- Tally responses: Available in Tally dashboard under form responses

---

## Troubleshooting

| Issue | Solution |
|---|---|
| Webhook not firing | Check Tally → Integrations → Webhooks is enabled and URL is correct |
| 401 on webhook | Verify `TALLY_SIGNING_SECRET` matches Tally's signing secret |
| VL registration fails | Check `VL_PUBLIC_TOKEN` and `VL_API_TOKEN` are correct |
| No referral email | Configure SMTP settings in `.env` or check `integration.log` |
| Hidden field not captured | Ensure the `ref` hidden field exists in Tally and is set to URL parameter |
| Dashboard shows 0 referrals | Verify `VL_CAMPAIGN_ID` is correct and participant email matches |

---

## File Structure

```
viral_loops_integration/
├── main.py                          # FastAPI webhook server
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Docker deployment
├── .env.example                     # Environment template
├── DEPLOYMENT_GUIDE.md              # This file
├── templates/
│   ├── index.html                   # Landing page
│   └── dashboard.html               # Participant referral dashboard
├── static/                          # Static assets (CSS, images)
└── scripts/
    ├── configure_campaign.py        # Campaign verification & setup
    └── setup_tally_hidden_field.py  # Add hidden ref field to Tally
```

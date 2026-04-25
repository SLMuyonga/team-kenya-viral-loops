#!/usr/bin/env python3
"""
Add a hidden 'ref' field to the Tally form for referral code tracking.
When someone clicks a referral link like:
  https://your-domain.com/refer/ABC123
They get redirected to:
  https://tally.so/r/eqN5PQ?ref=ABC123
The hidden field captures ABC123 and sends it in the webhook payload.

This script adds the hidden field to the existing Tally form.
"""

import json
import requests
import uuid

API_KEY = "tly-NAE1FQA1EzwErBBOdHqafgaIAkyhq3YI"
FORM_ID = "eqN5PQ"
BASE_URL = "https://api.tally.so"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# Fetch current form
print("Fetching current form...")
resp = requests.get(f"{BASE_URL}/forms/{FORM_ID}", headers=headers)
data = resp.json()
blocks = data["blocks"]
print(f"Current blocks: {len(blocks)}")

# Check if hidden field already exists
for block in blocks:
    payload = block.get("payload", {})
    full = json.dumps(payload)
    if "ref" in full.lower() and block.get("type") == "HIDDEN_FIELDS":
        print("Hidden 'ref' field already exists — no changes needed.")
        exit(0)

# Add hidden field block at the beginning (hidden fields go at top)
hidden_field_uuid = str(uuid.uuid4())
hidden_block = {
    "uuid": hidden_field_uuid,
    "type": "HIDDEN_FIELDS",
    "groupUuid": hidden_field_uuid,
    "groupType": "HIDDEN_FIELDS",
    "payload": {
        "fieldName": "ref",
        "prefill": "urlparameter",
        "urlParameterName": "ref"
    }
}

# Insert at position 0 (before everything)
blocks.insert(0, hidden_block)

# Patch
print("Adding hidden 'ref' field...")
resp = requests.patch(
    f"{BASE_URL}/forms/{FORM_ID}",
    headers=headers,
    json={"status": "PUBLISHED", "blocks": blocks}
)

if resp.status_code == 200:
    print(f"✅ Hidden 'ref' field added successfully!")
    print(f"   UUID: {hidden_field_uuid}")
    print(f"   The form will now capture ?ref=CODE from the URL")
    print(f"   Test: https://tally.so/r/{FORM_ID}?ref=TEST123")
else:
    print(f"❌ Failed: {resp.status_code} — {resp.text[:300]}")
    print("Note: Hidden fields may need to be added via the Tally dashboard instead.")
    print("Go to: Tally form editor → Add block → Hidden Fields → Name: 'ref' → Prefill: URL parameter")

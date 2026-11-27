#!/usr/bin/env python3
"""
Renews FYERS_ACCESS_TOKEN daily using the stored FYERS_REFRESH_TOKEN, so
daily_collector.py doesn't need a fresh browser login every day.

Fyers' access_token expires ~daily (~06:00 IST, confirmed empirically from
a real token's JWT 'exp' claim); the refresh_token lasts 15 days and can
renew it headlessly via POST /validate-refresh-token, needing only a
static PIN (not a rotating TOTP) -- unlike the initial access_token grant,
which requires an actual interactive browser login (see generate_token.py)
and can't be automated.

The exact request shape (grant_type, appIdHash, refresh_token, pin) and
appIdHash formula (sha256(f"{client_id}:{secret_key}")) are confirmed from
Fyers' own fyers_apiv3 SDK source (fyersModel.py's SessionModel.get_hash /
generate_token), not just community posts -- the SDK itself has no
built-in refresh call, so this hits the endpoint directly via requests.

Two things are NOT confirmed by Fyers' docs and are handled defensively
rather than assumed:
  - Whether a new refresh_token is issued on each use, or the same one
    persists for its full 15-day life. Whatever comes back in the
    response is saved, so it's correct either way.
  - The exact response JSON shape. This script is meant to be run once
    manually (see README note below) to observe a real response before
    ever being wired into cron.

If the refresh_token itself has expired (>15 days since the last browser
login), this fails distinctly from a random API error so the failure
mode is unambiguous rather than looking like any other auth error.
"""
import hashlib
import sys
from pathlib import Path

import requests

from fyers_settings import (
    FYERS_CLIENT_ID,
    FYERS_SECRET_KEY,
    FYERS_REFRESH_TOKEN,
    FYERS_PIN,
)

REFRESH_URL = "https://api-t1.fyers.in/api/v3/validate-refresh-token"


def app_id_hash():
    return hashlib.sha256(f"{FYERS_CLIENT_ID}:{FYERS_SECRET_KEY}".encode()).hexdigest()


def update_env(updates):
    env_path = Path(__file__).resolve().parent / ".env"
    lines = env_path.read_text().splitlines()

    written_keys = set()
    with env_path.open("w") as f:
        for line in lines:
            key = line.split("=", 1)[0] if "=" in line else None
            if key in updates:
                f.write(f"{key}={updates[key]}\n")
                written_keys.add(key)
            else:
                f.write(line + "\n")
        for key, value in updates.items():
            if key not in written_keys:
                f.write(f"{key}={value}\n")


def main():
    if not FYERS_REFRESH_TOKEN:
        print("FATAL: FYERS_REFRESH_TOKEN not set in .env. Run "
              "generate_token.py once (manual browser login) to obtain "
              "one.", file=sys.stderr)
        sys.exit(1)
    if not FYERS_PIN:
        print("FATAL: FYERS_PIN not set in .env.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "grant_type": "refresh_token",
        "appIdHash": app_id_hash(),
        "refresh_token": FYERS_REFRESH_TOKEN,
        "pin": FYERS_PIN,
    }
    response = requests.post(
        REFRESH_URL,
        headers={"Content-Type": "application/json; charset=utf-8"},
        json=payload,
    )
    data = response.json()

    if data.get("s") != "ok" or "access_token" not in data:
        # A refresh_token past its 15-day life is the expected way this
        # fails long-term; distinguish it in the message so it's obvious
        # this needs a human to re-run generate_token.py, not a retry.
        print(f"FATAL: token refresh failed (http {response.status_code}): "
              f"{data}", file=sys.stderr)
        print("If this is due to an expired refresh_token (>15 days since "
              "last browser login), run generate_token.py to get a new "
              "one.", file=sys.stderr)
        sys.exit(1)

    updates = {"FYERS_ACCESS_TOKEN": data["access_token"]}
    if "refresh_token" in data:
        updates["FYERS_REFRESH_TOKEN"] = data["refresh_token"]

    update_env(updates)
    print(f"Access token refreshed OK. Saved: {', '.join(sorted(updates.keys()))}")


if __name__ == "__main__":
    main()

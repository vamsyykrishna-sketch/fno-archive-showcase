#!/usr/bin/env python3
"""
Permanent utility (not a temp/delete-after-use script) -- checks the
current Fyers access token's real expiry by decoding its JWT payload,
rather than trusting whatever's in .env at face value. Confirms whether
a "refresh" actually landed on THIS box: generate_token.py only writes
.env when Fyers' auth-code exchange returns "s": "ok", so a failed
refresh leaves the old token in place with no loud failure signal --
this is the cheap, fast way to verify that didn't happen (see
conversation, 2026-09-07: a refresh was reported done but hadn't
actually landed, caught by exactly this check).

Usage: python3 check_token.py
Exit code: 0 if the token is currently valid, 1 if expired/invalid.
"""
import base64
import datetime
import json
import sys

from fyers_settings import FYERS_ACCESS_TOKEN

try:
    payload_b64 = FYERS_ACCESS_TOKEN.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    data = json.loads(base64.urlsafe_b64decode(payload_b64))
    exp = datetime.datetime.fromtimestamp(data["exp"], tz=datetime.timezone.utc).astimezone()
except Exception as e:
    print(f"Could not decode FYERS_ACCESS_TOKEN: {e}")
    sys.exit(1)

now = datetime.datetime.now().astimezone()
valid = exp > now

print("token expires:", exp)
print("current time: ", now)
print("still valid:  ", valid)
if valid:
    print("time remaining:", exp - now)

sys.exit(0 if valid else 1)

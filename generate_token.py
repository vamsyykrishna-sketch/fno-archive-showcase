from pathlib import Path

from fyers_auth import generate_auth_url, generate_access_token

# Delimiters + the URL alone on its own line make this easy to tap/select
# in a small mobile terminal (e.g. AWS Console's browser-based Session
# Manager), not just a desktop one.
print("\n" + "=" * 50)
print("STEP 1: Open this URL, log in, then come back here")
print("=" * 50)
print(generate_auth_url())
print("=" * 50)

auth_code = input("\nSTEP 2: Paste auth_code here: ").strip()

response = generate_access_token(auth_code)

print("\nResponse:\n")
print(response)

if response.get("s") == "ok":
    env_path = Path(__file__).resolve().parent / ".env"
    lines = env_path.read_text().splitlines()

    updates = {"FYERS_ACCESS_TOKEN": response["access_token"]}
    if "refresh_token" in response:
        updates["FYERS_REFRESH_TOKEN"] = response["refresh_token"]

    written_keys = set()
    with env_path.open("w") as f:
        for line in lines:
            key = line.split("=", 1)[0] if "=" in line else None
            if key in updates:
                f.write(f"{key}={updates[key]}\n")
                written_keys.add(key)
            else:
                f.write(line + "\n")
        # Any update key not already present as a line gets appended
        # (e.g. first time a refresh_token is ever saved).
        for key, value in updates.items():
            if key not in written_keys:
                f.write(f"{key}={value}\n")

    print(f"\nSaved to .env: {', '.join(sorted(updates.keys()))}")
    print("Note: Fyers' refresh-token API is currently disabled (SEBI algo "
          "trading rules, effective 2026-04-01) -- this login must be "
          "repeated daily. See AWS Console -> Systems Manager -> Session "
          "Manager for a mobile-friendly way to do this from a phone.")

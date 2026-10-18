"""Register Shopify webhooks that point at the WordSwift sync receiver.

Run once per store after deploying webhook_server.py somewhere publicly
reachable (Shopify must be able to POST to it over HTTPS).

Usage:
    export SHOPIFY_STORE=your-store.myshopify.com
    export SHOPIFY_TOKEN=shpat_xxxxxxxxxxxxxxxx
    export WEBHOOK_DESTINATION=https://your-sync-host.example.com/webhooks/shopify

    python3 register_webhooks.py            # register all product topics
    python3 register_webhooks.py --list     # show existing webhooks
    python3 register_webhooks.py --remove   # delete WordSwift webhooks

Requires only the standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-10")

TOPICS = [
    "products/create",
    "products/update",
    "products/delete",
]


def _request(method: str, url: str, token: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "X-Shopify-Access-Token": token,
        "Accept": "application/json",
    }
    if data:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return 0, f"cannot reach Shopify: {exc.reason}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Register Shopify webhooks for WordSwift")
    parser.add_argument("--list", action="store_true", help="List existing webhooks")
    parser.add_argument("--remove", action="store_true", help="Remove WordSwift webhooks")
    args = parser.parse_args()

    store = os.environ.get("SHOPIFY_STORE", "").strip()
    token = os.environ.get("SHOPIFY_TOKEN", "").strip()
    destination = os.environ.get("WEBHOOK_DESTINATION", "").strip()

    if not store or not token:
        print("ERROR: set SHOPIFY_STORE and SHOPIFY_TOKEN", file=sys.stderr)
        return 1
    if not args.list and not destination:
        print("ERROR: set WEBHOOK_DESTINATION (public HTTPS URL of webhook_server.py)",
              file=sys.stderr)
        return 1
    if destination and not destination.startswith("https://"):
        print("ERROR: Shopify requires an HTTPS destination; got "
              f"{destination!r}", file=sys.stderr)
        return 1

    base = f"https://{store}/admin/api/{API_VERSION}/webhooks.json"

    # ---- List ----
    status, payload = _request("GET", base, token)
    if status != 200:
        print(f"ERROR listing webhooks ({status}): {payload}", file=sys.stderr)
        return 1

    existing = payload.get("webhooks", [])
    if args.list:
        if not existing:
            print("No webhooks registered.")
            return 0
        print(f"{'TOPIC':<24} {'ID':<12} ADDRESS")
        print("-" * 80)
        for w in existing:
            print(f"{w.get('topic',''):<24} {w.get('id',''):<12} {w.get('address','')}")
        return 0

    # ---- Remove ----
    if args.remove:
        removed = 0
        for w in existing:
            if w.get("address") == destination:
                s, _ = _request("DELETE", f"{base}/{w['id']}.json", token)
                print(f"  {w['topic']}: {'removed' if s == 200 else f'failed ({s})'}")
                removed += 1 if s == 200 else 0
        print(f"Removed {removed} webhook(s).")
        return 0

    # ---- Register ----
    already = {(w.get("topic"), w.get("address")) for w in existing}

    print(f"Registering webhooks → {destination}\n")
    for topic in TOPICS:
        if (topic, destination) in already:
            print(f"  {topic}: already registered, skipping")
            continue

        status, payload = _request("POST", base, token, {
            "webhook": {
                "topic": topic,
                "address": destination,
                "format": "json",
                # Shopify signs each delivery with the app's client secret and
                # sends it as X-Shopify-Hmac-Sha256. webhook_server.py verifies
                # that signature using SHOPIFY_WEBHOOK_SECRET — set it to the
                # same client secret (NOT the shpat_ token used here).
            }
        })

        if status in (200, 201):
            print(f"  {topic}: registered (id={payload['webhook']['id']})")
        elif status == 422:
            print(f"  {topic}: rejected — {payload}", file=sys.stderr)
        else:
            print(f"  {topic}: FAILED ({status}) — {payload}", file=sys.stderr)

    print("\nDone. Verify with: python3 register_webhooks.py --list")
    print("\nIMPORTANT: the receiver needs the app's CLIENT SECRET, not this token.")
    print("Set SHOPIFY_WEBHOOK_SECRET on webhook_server.py to the 'API secret key'")
    print("from Shopify Admin → Settings → Apps → your app. The shpat_ token used")
    print("here is only for calling the Admin API and will NOT verify signatures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

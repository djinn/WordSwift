"""Shopify webhook receiver — keeps the WordSwift index in sync in real time.

Registers for products/create, products/update, products/delete and re-indexes
a single document per event instead of a full resync.

Run it:
    export SHOPIFY_WEBHOOK_SECRET=xxxxxxxxxxxx      # app's client secret
    export WORDSWIFT_URL=http://localhost:7700
    export WORDSWIFT_INDEX=shopify_products
    python3 webhook_server.py --port 8000

Then register the webhooks (see register_webhooks.py).

IMPORTANT — which secret? Shopify computes X-Shopify-Hmac-Sha256 with the
app's **client secret** (Settings → Apps → your app → "API secret key"), NOT
the Admin API access token (shpat_...). Setting SHOPIFY_TOKEN here will make
every verification fail. If you only have a token, you have the wrong key.

Shopify retries failed deliveries, but retries are not guaranteed to cover
prolonged outages — pair this with a scheduled `sync.py --full` as a safety net.

Only the standard library is used (http.server), so this runs anywhere. For
production, put it behind a real WSGI/ASGI server or a reverse proxy.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wordswift_client import (  # noqa: E402
    WordSwiftClient,
    WordSwiftError,
    extract_numeric_id,
)
from sync import transform  # noqa: E402

# Shopify signs webhooks with the app's client secret (API secret key).
WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "").strip()
WORDSWIFT_URL = os.environ.get("WORDSWIFT_URL", "http://localhost:7700")
WORDSWIFT_INDEX = os.environ.get("WORDSWIFT_INDEX", "shopify_products")
WORDSWIFT_DIM = int(os.environ.get("WORDSWIFT_DIM", "100"))

# Shopify sends the body raw; we must verify the HMAC over those exact bytes.
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB — product payloads are far smaller


def verify_hmac(body: bytes, hmac_header: Optional[str]) -> bool:
    """Verify Shopify's X-Shopify-Hmac-Sha256 signature.

    Returns True when the signature matches. When the secret is unset we
    cannot verify, so we refuse the request rather than accept it blindly —
    an unauthenticated endpoint would let anyone mutate your index.
    """
    if not hmac_header:
        return False
    if not WEBHOOK_SECRET:
        print("WARNING: SHOPIFY_WEBHOOK_SECRET not set — cannot verify webhooks",
              file=sys.stderr)
        return False

    digest = hmac.new(WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, hmac_header)


def handle_event(topic: str, payload: Dict[str, Any]) -> str:
    """Apply one webhook event to the index. Returns a human-readable status."""
    client = WordSwiftClient(WORDSWIFT_URL)

    if topic == "products/delete":
        product_id = extract_numeric_id(payload.get("id"))
        if product_id is None:
            return "skipped: no usable id in delete payload"

        # NOTE: WordSwift has no per-document delete endpoint. The document
        # stays searchable until the next full resync. We record the id so a
        # downstream filter can hide it — see docs/ECOMMERCE.md.
        record_tombstone(product_id)
        return (
            f"tombstoned product {product_id} (WordSwift has no per-document "
            f"delete; hidden by filter until next full resync)"
        )

    if topic in ("products/create", "products/update"):
        # Shopify sends the product object as the top-level payload for
        # products/create and products/update. Some apps and the GraphQL
        # variants nest it under "product" — accept both.
        product = payload.get("product", payload) if isinstance(payload, dict) else {}
        doc = transform(product)

        if doc is None:
            # Draft/archived: remove from search by tombstoning.
            pid = extract_numeric_id(product.get("id"))
            if pid is not None:
                record_tombstone(pid)
                return f"product {pid} became unsearchable; tombstoned"
            return "skipped: product not searchable"

        client.create_index(WORDSWIFT_INDEX, dim=WORDSWIFT_DIM)
        client.add_documents(WORDSWIFT_INDEX, [doc])
        clear_tombstone(doc["id"])
        return f"indexed product {doc['id']}: {doc['title'][:60]}"

    return f"ignored topic {topic}"


# ----------------------------------------------------------------------
# Tombstones — hides deleted products from results
# ----------------------------------------------------------------------
# WordSwift's API only deletes whole indexes, so deleted products linger in the
# full-text index. We persist their ids and filter them out at query time.
TOMBSTONE_PATH = os.environ.get(
    "WORDSWIFT_TOMBSTONES", "/tmp/wordswift_tombstones.json"
)


def _load_tombstones() -> set:
    try:
        with open(TOMBSTONE_PATH) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_tombstones(ids: set) -> None:
    tmp = TOMBSTONE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(ids), f)
    os.replace(tmp, TOMBSTONE_PATH)


def record_tombstone(product_id: int) -> None:
    ids = _load_tombstones()
    ids.add(product_id)
    _save_tombstones(ids)


def clear_tombstone(product_id: int) -> None:
    ids = _load_tombstones()
    if product_id in ids:
        ids.discard(product_id)
        _save_tombstones(ids)


def filter_deleted(hits: list) -> list:
    """Remove tombstoned products from search results."""
    dead = _load_tombstones()
    if not dead:
        return hits
    return [h for h in hits if h.get("id") not in dead]


# ----------------------------------------------------------------------
# HTTP server
# ----------------------------------------------------------------------
class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "WordSwiftShopifyWebhook/1.0"

    def _respond(self, status: int, body: Dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("", "/health"):
            self._respond(200, {"status": "ok", "index": WORDSWIFT_INDEX})
            return
        if self.path.rstrip("/") == "/search":
            self._respond(400, {"error": "use POST /search with {\"q\": ...}"})
            return
        self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")

        if path == "/search":
            self._handle_search()
            return

        if path != "/webhooks/shopify":
            self._respond(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._respond(400, {"error": "missing or oversized body"})
            return

        body = self.rfile.read(length)

        if not verify_hmac(body, self.headers.get("X-Shopify-Hmac-Sha256")):
            self._respond(401, {"error": "invalid HMAC signature"})
            return

        topic = self.headers.get("X-Shopify-Topic", "")
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        try:
            status = handle_event(topic, payload)
        except WordSwiftError as exc:
            # 500 makes Shopify retry the delivery.
            print(f"ERROR indexing: {exc}", file=sys.stderr)
            self._respond(500, {"error": str(exc)})
            return

        print(f"[{topic}] {status}", file=sys.stderr)
        self._respond(200, {"status": "ok", "detail": status})

    def _handle_search(self) -> None:
        """Storefront search proxy — filters tombstoned products."""
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        query = (payload.get("q") or "").strip()
        if not query:
            self._respond(400, {"error": "missing q"})
            return

        try:
            hits = filter_deleted(
                WordSwiftClient(WORDSWIFT_URL).search(
                    WORDSWIFT_INDEX, query,
                    limit=int(payload.get("limit", 10)),
                    hybrid=bool(payload.get("hybrid", True)),
                )
            )
        except WordSwiftError as exc:
            self._respond(exc.status or 502, {"error": exc.detail})
            return

        self._respond(200, {"query": query, "hits": hits, "count": len(hits)})

    def log_message(self, fmt: str, *args: Any) -> None:
        """Route access logs to stderr with the topic prefix."""
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Shopify webhook receiver for WordSwift")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if not WEBHOOK_SECRET:
        print("WARNING: SHOPIFY_WEBHOOK_SECRET is not set. All webhooks will be "
              "rejected with 401.", file=sys.stderr)

    server = ThreadingHTTPServer((args.host, args.port), WebhookHandler)
    print(f"Listening on http://{args.host}:{args.port}", file=sys.stderr)
    print(f"  POST /webhooks/shopify   — product events", file=sys.stderr)
    print(f"  POST /search             — storefront search proxy", file=sys.stderr)
    print(f"  Index: {WORDSWIFT_INDEX} @ {WORDSWIFT_URL}", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

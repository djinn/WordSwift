"""Shopify → WordSwift product sync.

Pulls products from the Shopify Admin REST API and indexes them into WordSwift
for typo-tolerant, BM25-ranked storefront search.

Setup:
    1. Shopify Admin → Settings → Apps and sales channels → Develop apps
    2. Create an app, grant scopes: read_products
    3. Install the app and copy the Admin API access token (shpat_...)

Usage:
    export SHOPIFY_STORE=your-store.myshopify.com
    export SHOPIFY_TOKEN=shpat_xxxxxxxxxxxxxxxx
    export WORDSWIFT_URL=http://localhost:7700

    python3 sync.py --full              # index every product
    python3 sync.py --full --rebuild    # delete the index first, then re-index
    python3 sync.py --product-id 123    # re-index a single product
    python3 sync.py --stats             # show index stats
    python3 sync.py --dry-run --full    # fetch + transform, don't index

Requires only the standard library.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

# Allow running from the integrations/shopify directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

from wordswift_client import (  # noqa: E402
    WordSwiftClient,
    WordSwiftError,
    extract_numeric_id,
    stringify_document,
    wait_for_health,
)

API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-10")
PAGE_LIMIT = 250  # Shopify's hard maximum


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
class Config:
    def __init__(self) -> None:
        self.store = os.environ.get("SHOPIFY_STORE", "").strip()
        self.token = os.environ.get("SHOPIFY_TOKEN", "").strip()
        self.wordswift_url = os.environ.get("WORDSWIFT_URL", "http://localhost:7700")
        self.index = os.environ.get("WORDSWIFT_INDEX", "shopify_products")
        self.dim = int(os.environ.get("WORDSWIFT_DIM", "100"))

        missing = [
            name
            for name, val in (("SHOPIFY_STORE", self.store), ("SHOPIFY_TOKEN", self.token))
            if not val
        ]
        if missing:
            raise SystemExit(
                f"Missing required environment variable(s): {', '.join(missing)}\n"
                "Set SHOPIFY_STORE and SHOPIFY_TOKEN before running."
            )

    @property
    def base(self) -> str:
        return f"https://{self.store}/admin/api/{API_VERSION}"


# ----------------------------------------------------------------------
# Shopify API
# ----------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(raw: Optional[str]) -> str:
    """Convert Shopify's body_html into plain text for indexing."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def shopify_get(url: str, token: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GET a Shopify Admin endpoint, returning parsed JSON."""
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(
        url,
        headers={
            "X-Shopify-Access-Token": token,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Shopify API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach Shopify: {exc.reason}") from exc


def parse_link_header(header: Optional[str]) -> Optional[str]:
    """Extract rel="next" from Shopify's pagination Link header."""
    if not header:
        return None
    for part in header.split(","):
        if 'rel="next"' in part:
            match = re.search(r"<([^>]+)>", part)
            if match:
                return match.group(1)
    return None


def iter_products(cfg: Config) -> Iterator[Dict[str, Any]]:
    """Yield every product, following pagination automatically."""
    url = f"{cfg.base}/products.json"
    params: Dict[str, Any] = {"limit": PAGE_LIMIT, "fields": _PRODUCT_FIELDS}
    page = 0

    while url:
        page += 1
        req = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode(params)}" if params else url,
            headers={"X-Shopify-Access-Token": cfg.token, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                next_url = parse_link_header(resp.headers.get("Link"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"Shopify API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"Cannot reach Shopify: {exc.reason}") from exc

        products = payload.get("products", [])
        print(f"  page {page}: {len(products)} products", file=sys.stderr)
        yield from products

        # Shopify returns the next page as a full URL with its own query string.
        url = next_url
        params = {}


_PRODUCT_FIELDS = ",".join(
    [
        "id", "title", "body_html", "vendor", "product_type",
        "tags", "handle", "variants", "images", "status", "options",
    ]
)


# ----------------------------------------------------------------------
# Transform: Shopify product → WordSwift document
# ----------------------------------------------------------------------
def transform(product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a Shopify product to the WordSwift document schema.

    Returns None for products that should not be searchable (drafts, archived).
    """
    if product.get("status") not in (None, "active"):
        return None

    doc_id = extract_numeric_id(product.get("id"))
    if doc_id is None:
        return None

    variants = product.get("variants") or []
    first_variant = variants[0] if variants else {}

    images = product.get("images") or []
    image_url = images[0].get("src", "") if images else ""

    # Option values (size/colour) are strong search signals.
    option_text = " ".join(
        " ".join(str(v) for v in opt.get("values", []))
        for opt in (product.get("options") or [])
        if opt.get("name", "").lower() not in ("title",)
    )

    tags = product.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    doc = {
        "id": doc_id,
        "title": product.get("title", ""),
        "description": strip_html(product.get("body_html")),
        "vendor": product.get("vendor", ""),
        "product_type": product.get("product_type", ""),
        "tags": ", ".join(tags),
        "handle": product.get("handle", ""),
        "sku": first_variant.get("sku", ""),
        "barcode": first_variant.get("barcode", "") or "",
        "price": first_variant.get("price", ""),
        "compare_at_price": first_variant.get("compare_at_price", "") or "",
        "available": bool(first_variant.get("inventory_quantity", 1) > 0)
        if "inventory_quantity" in first_variant
        else True,
        "image": image_url,
        "options": option_text,
        "variant_count": len(variants),
    }

    # Only string fields get indexed by WordSwift; stringify_document handles
    # the coercion and skips None values.
    return stringify_document(doc)


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def cmd_full_sync(cfg: Config, rebuild: bool, dry_run: bool) -> int:
    client = WordSwiftClient(cfg.wordswift_url)

    if not wait_for_health(cfg.wordswift_url, timeout=20):
        print(f"ERROR: WordSwift not reachable at {cfg.wordswift_url}", file=sys.stderr)
        print("       Start it with: docker compose up -d", file=sys.stderr)
        return 1

    if rebuild:
        print(f"Deleting existing index '{cfg.index}'...", file=sys.stderr)
        client.delete_index(cfg.index)

    client.create_index(cfg.index, dim=cfg.dim)

    print(f"Fetching products from {cfg.store}...", file=sys.stderr)
    docs: List[Dict[str, Any]] = []
    skipped = 0
    started = time.time()

    for product in iter_products(cfg):
        doc = transform(product)
        if doc is None:
            skipped += 1
            continue
        docs.append(doc)

    elapsed = time.time() - started
    print(f"\nTransformed {len(docs)} products in {elapsed:.1f}s ({skipped} skipped)",
          file=sys.stderr)

    if dry_run:
        print("\n--dry-run: first document that would be indexed:", file=sys.stderr)
        print(json.dumps(docs[0], indent=2) if docs else "(none)", file=sys.stderr)
        return 0

    if not docs:
        print("Nothing to index.", file=sys.stderr)
        return 0

    print(f"Indexing into '{cfg.index}'...", file=sys.stderr)
    added = client.add_documents(cfg.index, docs)

    stats = client.stats(cfg.index)
    print(
        f"\nDone. {added}/{len(docs)} documents indexed "
        f"(fullTextDocuments={stats.get('fullTextDocuments')})",
        file=sys.stderr,
    )
    return 0


def cmd_single_product(cfg: Config, product_id: str) -> int:
    client = WordSwiftClient(cfg.wordswift_url)
    client.create_index(cfg.index, dim=cfg.dim)

    raw = shopify_get(f"{cfg.base}/products/{product_id}.json", cfg.token)
    doc = transform(raw.get("product", {}))
    if doc is None:
        print(f"Product {product_id} is not searchable (inactive or bad id).",
              file=sys.stderr)
        return 1

    client.add_documents(cfg.index, [doc])
    print(f"Indexed product {product_id}: {doc['title']}", file=sys.stderr)
    return 0


def cmd_stats(cfg: Config) -> int:
    client = WordSwiftClient(cfg.wordswift_url)
    try:
        stats = client.stats(cfg.index)
    except WordSwiftError as exc:
        if exc.status == 404:
            print(f"Index '{cfg.index}' does not exist. Run: sync.py --full",
                  file=sys.stderr)
            return 1
        raise

    print(json.dumps(stats, indent=2))
    return 0


def cmd_test_search(cfg: Config, query: str) -> int:
    client = WordSwiftClient(cfg.wordswift_url)
    hits = client.search(cfg.index, query, limit=5, hybrid=True)
    if not hits:
        print(f"No results for '{query}'")
        return 0

    print(f"Results for '{query}':\n")
    for h in hits:
        print(f"  {h.get('score', 0):.4f}  [{h.get('id')}] {h.get('title')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync Shopify products into WordSwift",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--full", action="store_true", help="Sync all products")
    parser.add_argument("--rebuild", action="store_true",
                        help="Delete the index before syncing (with --full)")
    parser.add_argument("--product-id", help="Re-index a single product by id")
    parser.add_argument("--stats", action="store_true", help="Show index stats")
    parser.add_argument("--test-search", metavar="QUERY",
                        help="Run a test search against the index")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and transform but do not index")
    args = parser.parse_args()

    cfg = Config()

    if args.full:
        return cmd_full_sync(cfg, rebuild=args.rebuild, dry_run=args.dry_run)
    if args.product_id:
        return cmd_single_product(cfg, args.product_id)
    if args.stats:
        return cmd_stats(cfg)
    if args.test_search:
        return cmd_test_search(cfg, args.test_search)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

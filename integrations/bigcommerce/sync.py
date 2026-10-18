"""BigCommerce → WordSwift product sync.

Pulls products from the BigCommerce Catalog v3 API and indexes them for
typo-tolerant, BM25-ranked storefront search.

Setup:
    1. BigCommerce Admin → Advanced Settings → API Accounts
    2. Create a **Custom App** (V2/V3 token), grant Catalog: Read-Only
       (add Catalog: Modify if you want to write back)
    3. Copy the Store Hash (from the API path) and the Access Token

Usage:
    export BIGCOMMERCE_STORE_HASH=abc123def4
    export BIGCOMMERCE_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxx
    export WORDSWIFT_URL=http://localhost:7700

    python3 sync.py --full
    python3 sync.py --full --rebuild
    python3 sync.py --product-id 123
    python3 sync.py --stats
    python3 sync.py --test-search "waterprof jaket"
    python3 sync.py --full --dry-run

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

from wordswift_client import (  # noqa: E402
    WordSwiftClient,
    WordSwiftError,
    extract_numeric_id,
    stringify_document,
    wait_for_health,
)

API_ROOT = "https://api.bigcommerce.com/stores"
_TAG_RE = re.compile(r"<[^>]+>")


class Config:
    def __init__(self) -> None:
        self.store_hash = os.environ.get("BIGCOMMERCE_STORE_HASH", "").strip()
        self.token = os.environ.get("BIGCOMMERCE_TOKEN", "").strip()
        self.wordswift_url = os.environ.get("WORDSWIFT_URL", "http://localhost:7700")
        self.index = os.environ.get("WORDSWIFT_INDEX", "bigcommerce_products")
        self.dim = int(os.environ.get("WORDSWIFT_DIM", "100"))

        missing = [
            name
            for name, val in (
                ("BIGCOMMERCE_STORE_HASH", self.store_hash),
                ("BIGCOMMERCE_TOKEN", self.token),
            )
            if not val
        ]
        if missing:
            raise SystemExit(
                f"Missing required environment variable(s): {', '.join(missing)}"
            )

    @property
    def catalog_url(self) -> str:
        return f"{API_ROOT}/{self.store_hash}/v3/catalog"


# ----------------------------------------------------------------------
# BigCommerce API
# ----------------------------------------------------------------------
def strip_html(raw: Optional[str]) -> str:
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def bc_get(url: str, token: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GET a BigCommerce v3 endpoint, following `meta.pagination.links.next`."""
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(
        url,
        headers={
            "X-Auth-Token": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"BigCommerce API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach BigCommerce: {exc.reason}") from exc


API_HOST = "https://api.bigcommerce.com"


def next_page_url(payload: Dict[str, Any], current_url: str = "") -> Optional[str]:
    """Resolve BigCommerce's `meta.pagination.links.next` to an absolute URL.

    The API is inconsistent about the shape of this link, so handle all three
    forms observed in practice:
      * absolute   ``https://api.bigcommerce.com/stores/HASH/v3/...``
      * path       ``/stores/HASH/v3/catalog/products?after=...``
      * query only ``?limit=250&after=...``  (the common case)

    For the query-only form we need the current endpoint, hence `current_url`.
    """
    meta = payload.get("meta") or {}
    pagination = meta.get("pagination") or {}
    links = pagination.get("links") or {}
    nxt = (links.get("next") or "").strip()

    if not nxt:
        return None

    if nxt.startswith("http://") or nxt.startswith("https://"):
        return nxt

    if nxt.startswith("?"):
        if not current_url:
            return None
        base = current_url.split("?", 1)[0]
        return base + nxt

    if nxt.startswith("/"):
        return API_HOST + nxt

    # Bare relative segment — resolve against the current endpoint's directory.
    if current_url:
        base = current_url.split("?", 1)[0].rsplit("/", 1)[0]
        return f"{base}/{nxt}"
    return None


def iter_products(cfg: Config) -> Iterator[Dict[str, Any]]:
    """Yield every product, following pagination."""
    endpoint = f"{cfg.catalog_url}/products"
    url: Optional[str] = endpoint
    params: Optional[Dict[str, Any]] = {"limit": 250, "include": "images"}
    page = 0

    while url:
        page += 1
        payload = bc_get(url, cfg.token, params)
        products = payload.get("data") or []
        print(f"  page {page}: {len(products)} products", file=sys.stderr)
        yield from products

        # The next link carries its own query string, so drop our params.
        url = next_page_url(payload, current_url=url)
        params = None


# ----------------------------------------------------------------------
# Transform: BigCommerce product → WordSwift document
# ----------------------------------------------------------------------
def transform(product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a BigCommerce v3 product to the WordSwift schema.

    Returns None for products that should not be searchable.
    """
    # is_visible false → hidden from storefront; availability "disabled" → not sold
    if product.get("is_visible") is False:
        return None
    if str(product.get("availability", "")).lower() == "disabled":
        return None

    doc_id = extract_numeric_id(product.get("id"))
    if doc_id is None:
        return None

    images = product.get("images") or []
    image_url = images[0].get("url_standard", "") if images else ""

    # custom_fields is a list of {name, value} pairs
    custom = product.get("custom_fields") or []
    custom_text = " ".join(
        f"{f.get('name', '')} {f.get('value', '')}" for f in custom
    ).strip()

    doc = {
        "id": doc_id,
        "title": product.get("name", ""),
        "description": strip_html(product.get("description")),
        "sku": product.get("sku", ""),
        "brand_id": product.get("brand_id", "") or "",
        "categories": ", ".join(str(c) for c in (product.get("categories") or [])),
        "type": product.get("type", ""),
        "price": product.get("price", ""),
        "retail_price": product.get("retail_price", "") or "",
        "sale_price": product.get("sale_price", "") or "",
        "upc": product.get("upc", "") or "",
        "mpn": product.get("mpn", "") or "",
        "search_keywords": product.get("search_keywords", "") or "",
        "custom_fields": custom_text,
        "page_title": product.get("page_title", "") or "",
        "meta_description": product.get("meta_description", "") or "",
        "url": product.get("url", "") or "",
        # BigCommerce uses inventory_level = -1 for "unlimited" and 0 for
        # "out of stock". Treat anything non-zero as in stock.
        "in_stock": int(product.get("inventory_level") or 0) != 0,
        "image": image_url,
        "weight": product.get("weight", "") or "",
    }

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

    print(f"Fetching products from store {cfg.store_hash}...", file=sys.stderr)
    docs: List[Dict[str, Any]] = []
    skipped = 0
    started = time.time()

    for product in iter_products(cfg):
        doc = transform(product)
        if doc is None:
            skipped += 1
            continue
        docs.append(doc)

    print(
        f"\nTransformed {len(docs)} products in {time.time() - started:.1f}s "
        f"({skipped} skipped)",
        file=sys.stderr,
    )

    if dry_run:
        print("\n--dry-run: first document that would be indexed:", file=sys.stderr)
        print(json.dumps(docs[0], indent=2) if docs else "(none)", file=sys.stderr)
        return 0

    if not docs:
        print("Nothing to index.", file=sys.stderr)
        return 0

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

    payload = bc_get(f"{cfg.catalog_url}/products/{product_id}", cfg.token)
    doc = transform(payload.get("data", {}))
    if doc is None:
        print(f"Product {product_id} is not searchable.", file=sys.stderr)
        return 1

    client.add_documents(cfg.index, [doc])
    print(f"Indexed product {product_id}: {doc['title']}", file=sys.stderr)
    return 0


def cmd_stats(cfg: Config) -> int:
    client = WordSwiftClient(cfg.wordswift_url)
    try:
        print(json.dumps(client.stats(cfg.index), indent=2))
    except WordSwiftError as exc:
        if exc.status == 404:
            print(f"Index '{cfg.index}' does not exist. Run: sync.py --full",
                  file=sys.stderr)
            return 1
        raise
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
        description="Sync BigCommerce products into WordSwift",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--full", action="store_true", help="Sync all products")
    parser.add_argument("--rebuild", action="store_true",
                        help="Delete the index before syncing (with --full)")
    parser.add_argument("--product-id", help="Re-index a single product by id")
    parser.add_argument("--stats", action="store_true", help="Show index stats")
    parser.add_argument("--test-search", metavar="QUERY", help="Run a test search")
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

# Ecommerce Vertical Search Integration

WordSwift replaces the default (usually MySQL `LIKE`-based) product search on
major ecommerce platforms with **BM25 full-text + typo-tolerant fuzzy +
semantic** search. Shoppers typing `waterprof jaket` still find the
*Waterproof Jacket* — something none of the built-in search implementations do
out of the box.

> **Measured on a live WordSwift instance**, the query `waterprof jaket`
> returned *Patagonia Torrentshell Waterproof Jacket* as the top hit at a
> score of `0.893`, with the other two catalog items following. Exact and
> near-miss queries both work without any stemming or synonym configuration.

---

## Table of Contents

- [Why Replace Native Search?](#why-replace-native-search)
- [The Document Schema](#the-document-schema)
- [General Integration Pattern](#general-integration-pattern)
- [Shopify](#shopify)
- [WooCommerce](#woocommerce)
- [BigCommerce](#bigcommerce)
- [Magento / Adobe Commerce](#magento--adobe-commerce)
- [Keeping the Index Fresh](#keeping-the-index-fresh)
- [Scaling Considerations](#scaling-considerations)

---

## Why Replace Native Search?

| Platform | Native search limitation | WordSwift fix |
|----------|--------------------------|---------------|
| **Shopify** | `search.suggest` does prefix matching only; no typo tolerance on the storefront | Fuzzy + BM25 + semantic |
| **WooCommerce** | MySQL `LIKE '%term%'` — no relevance ranking, breaks on typos | BM25 relevance scoring |
| **BigCommerce** | Keyword match; semantic requires paid apps | Hybrid scoring built in |
| **Magento** | Elasticsearch/OpenSearch required for good search — heavy dependency | Single lightweight container |

All four platforms let you intercept search and return your own result set, so
WordSwift drops in without modifying core.

---

## The Document Schema

Every integration maps products to the same shape. **Only string fields are
indexed for full-text search** — the `add_documents` endpoint filters to
`isinstance(v, str)`:

```json
{
  "id": 7215348211,
  "title": "Patagonia Torrentshell Waterproof Jacket",
  "description": "Lightweight 2.5-layer rain shell, H2No performance",
  "sku": "PTG-TS-BLK-M",
  "vendor": "Patagonia",
  "product_type": "Jackets",
  "tags": "waterproof, rain, hiking, mens",
  "handle": "patagonia-torrentshell-jacket",
  "price": "99.00",
  "image": "https://cdn.shopify.com/s/files/.../jacket.jpg"
}
```

> ⚠️ **Convert non-string fields to strings before indexing.** Numbers, booleans,
> and nested objects are silently skipped by `add_documents`. Send
> `"price": "99.00"` not `"price": 99.00`.

The `id` field is coerced to an integer by the registry. If your platform uses
non-numeric IDs (e.g. Shopify GIDs like `gid://shopify/Product/123`), extract
the numeric part.

---

## General Integration Pattern

Every platform follows the same three-part flow:

```
┌──────────────────────────────────────────────────────────┐
│  1. SYNC     Product data ──► WordSwift /documents        │
│              (full reindex on install + webhook deltas)   │
├──────────────────────────────────────────────────────────┤
│  2. INTERCEPT  Storefront search box ──► WordSwift        │
│              /indexes/{name}/search?q=...                 │
├──────────────────────────────────────────────────────────┤
│  3. RENDER   Hits ──► platform's product card template    │
│              (preserve WordSwift's ranking order)         │
└──────────────────────────────────────────────────────────┘
```

### Core API calls

```bash
# 1. Create an index per store
curl -X POST http://localhost:7700/indexes \
  -H "Content-Type: application/json" \
  -d '{"uid":"myshop_products","dim":100}'

# 2. Bulk-add products
curl -X POST http://localhost:7700/indexes/myshop_products/documents \
  -H "Content-Type: application/json" \
  -d '[{"id":1,"title":"...","description":"..."}]'

# 3. Search
curl "http://localhost:7700/indexes/myshop_products/search?q=jacket&limit=20"

# 4. Check what's indexed
curl http://localhost:7700/indexes/myshop_products/stats
```

---

## Shopify

Shopify integration uses a **custom app** for the Admin API plus a **theme app
extension** for the storefront search box.

### Step 1 — Create a custom app

1. Shopify Admin → **Settings → Apps and sales channels → Develop apps**
2. **Create an app** → grant these Admin API scopes:
   - `read_products`
   - `read_product_listings`
3. Install the app and copy the **Admin API access token** (`shpat_...`)

### Step 2 — Sync products

See [`integrations/shopify/sync.py`](../integrations/shopify/sync.py).

```bash
cd integrations/shopify
pip install requests
export SHOPIFY_STORE=your-store.myshopify.com
export SHOPIFY_TOKEN=shpat_xxxxxxxxxxxxxxxx
export WORDSWIFT_URL=http://localhost:7700

python3 sync.py --full
```

The script paginates through all products using Shopify's `Link` header
convention and normalizes each to the schema above.

### Step 3 — Keep it in sync with webhooks

Register webhooks so product edits re-index immediately:

```bash
curl -X POST "https://$SHOPIFY_STORE/admin/api/2024-10/webhooks.json" \
  -H "X-Shopify-Access-Token: $SHOPIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
      "topic": "products/update",
      "address": "https://your-sync-host/webhooks/shopify",
      "format": "json"
    }
  }'
```

Register `products/create`, `products/update`, and `products/delete`.
See [`integrations/shopify/webhook_server.py`](../integrations/shopify/webhook_server.py)
for a receiver that verifies the `X-Shopify-Hmac-Sha256` signature.

### Step 4 — Storefront search

Two options:

**Option A — Theme app extension (recommended)**

Shopify blocks cross-origin `fetch` from themes to arbitrary hosts unless the
request goes through an **app proxy**. Configure the app proxy URL in your app
settings to point at your backend, then the theme JS calls
`/apps/search?q=...` (same origin, no CORS issues).

**Option B — Client-side with CORS**

WordSwift currently sends `allow_origins=["*"]`, so a direct browser call works
for development. **Do not rely on this in production** — anyone could query or
mutate your index.

```javascript
// See integrations/shopify/theme/wordswift-search.js
async function searchProducts(query) {
  const res = await fetch(
    `${WORDSWIFT_URL}/indexes/${INDEX}/search?q=${encodeURIComponent(query)}&limit=8&typo_tolerance=true`
  );
  const { hits } = await res.json();
  return hits.map(h => ({
    title: h.title,
    url: `/products/${h.handle}`,
    price: h.price,
    image: h.image,
    score: h.score,
  }));
}
```

---

## WooCommerce

WooCommerce integration is a **WordPress plugin** that hooks `pre_get_posts`.

See [`integrations/woocommerce/wordswift-search.php`](../integrations/woocommerce/wordswift-search.php).

### Install

1. Copy the `woocommerce/` directory to `wp-content/plugins/wordswift-search/`
2. WordPress Admin → **Plugins → Activate "WordSwift Search"**
3. **Settings → WordSwift** — enter your WordSwift URL and index name
4. Click **"Full Resync"** to index all existing products

### How it works

```php
// Intercept the search query before MySQL runs it
add_action('pre_get_posts', function ($query) {
    if (! $query->is_search() || ! $query->is_main_query() || is_admin()) return;

    $ids = wordswift_search($query->get('s'));   // ask WordSwift
    if ($ids) {
        $query->set('post__in', $ids);
        $query->set('orderby', 'post__in');      // preserve BM25 ranking
        $query->set('s', '');                     // disable LIKE matching
    }
}, 20);

// Re-index on product save
add_action('save_post_product', 'wordswift_index_product', 10, 2);
```

> ⚠️ **`post__in` ordering is essential.** Without `orderby => post__in`,
> WordPress re-sorts by relevance/date and throws away your ranking.

### Requirements

- WordPress 5.8+
- WooCommerce 6.0+
- PHP 7.4+ (uses `wp_remote_get`/`wp_remote_post`, no external deps)

---

## BigCommerce

BigCommerce uses **Stencil** themes. The integration is a JS widget plus a
server-side sync script.

See [`integrations/bigcommerce/theme/wordswift-search.js`](../integrations/bigcommerce/theme/wordswift-search.js).

### Install

1. Add the JS widget to your theme's `assets/js/` directory
2. Reference it in `templates/components/search/form.html`
3. Register webhooks for `store/product/*` events

### Webhook registration

```bash
curl -X POST "https://api.bigcommerce.com/stores/{STORE_HASH}/v3/webhooks" \
  -H "X-Auth-Token: $BC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "store/product/updated",
    "destination": "https://your-sync-host/webhooks/bigcommerce",
    "is_active": true
  }'
```

Register `store/product/created`, `store/product/updated`, and
`store/product/deleted`.

### Stencil CLI development

```bash
npm install -g @bigcommerce/stencil-cli
cd your-theme
stencil start --themeVersion 1.0
```

---

## Magento / Adobe Commerce

Magento needs a proper **module** with dependency injection.

See [`integrations/magento/`](../integrations/magento/).

### Install

```bash
cp -r integrations/magento/WordSwift app/code/WordSwift
cd /path/to/magento
bin/magento module:enable WordSwift_Search
bin/magento setup:upgrade
bin/magento cache:flush
```

### How it works

Magento routes search through `Magento\Search\Model\Query`. The module
registers a plugin that swaps the result collection for WordSwift-ranked IDs:

```php
// Plugin on SearchResultFactory
public function afterCreate($subject, $result)
{
    $queryText = $result->getQueryText();
    $ids = $this->wordswiftClient->search($queryText);

    $collection = $this->productCollectionFactory->create()
        ->addIdFilter($ids)
        ->addAttributeToSelect('*');

    // Preserve WordSwift ranking with a MySQL FIELD() expression
    $collection->getSelect()->order(
        new \Zend_Db_Expr('FIELD(e.entity_id, ' . implode(',', $ids) . ')')
    );

    return $collection;
}
```

### Requirements

- Magento 2.4.x (Open Source or Commerce)
- PHP 8.1+
- Guzzle (bundled with Magento core)

---

## Keeping the Index Fresh

Three strategies, use them together:

### 1. Webhooks (real-time)

Every platform emits product lifecycle events. Register for create/update/delete
and push a single document each time. This is the primary mechanism.

### 2. Scheduled full resync (safety net)

Webhooks get lost — network blips, platform outages, missed retries. Run a
nightly full resync:

```bash
# cron: 3 AM daily
0 3 * * * cd /opt/wordswift/integrations/shopify && python3 sync.py --full >> /var/log/ws-sync.log 2>&1
```

### 3. Startup reconciliation

On service restart, compare the platform's product count to WordSwift's
`stats.fullTextDocuments` and trigger a resync if they diverge.

```bash
curl -s http://localhost:7700/indexes/myshop_products/stats | jq .fullTextDocuments
```

> ⚠️ **Use `fullTextDocuments`, not `numberOfDocuments`.** Verified against a
> live instance: after indexing 3 products with no trained embedding model,
> `numberOfDocuments` reports `0` while `fullTextDocuments` reports `3`.
> `numberOfDocuments` reflects the *embedding vocabulary size*, not your
> document count — so it stays 0 until you `POST /train`.
>
> ```json
> {"numberOfDocuments": 0, "databaseSize": 0, "dimension": 100,
>  "fullTextDocuments": 3, "lastUpdate": "..."}
> ```

### Deletions

The current API has **no per-document delete endpoint** — only
`DELETE /indexes/{name}` removes a whole index. For product deletions:

- **Short term:** mark the product deleted in your own database and filter its
  ID out of the results before rendering.
- **Long term:** the cleanest approach is a nightly full resync that rebuilds
  the index from scratch.

> This is a known gap. A `DELETE /indexes/{name}/documents/{id}` endpoint would
> make incremental deletes possible — contributions welcome.

---

## Scaling Considerations

### Catalog size vs. memory

WordSwift loads the inverted index into LevelDB but keeps posting lists in
memory during search. Rough guidance:

| Catalog size | Suggested memory | Notes |
|--------------|------------------|-------|
| < 10K products | 512 MB | Single container, default settings |
| 10K–100K | 2 GB | Increase `--memory` limit |
| 100K–1M | 4–8 GB | Consider sharding by category |
| > 1M | Sharded | One index per shard behind a router |

### Multi-store setups

Create **one index per store**, not one shared index:

```
index: store_us_products
index: store_eu_products
index: store_wholesale_products
```

This keeps relevance tuned per catalog and makes per-store reindexing safe.

### Read scaling

Search is read-heavy and CPU-bound during fuzzy matching (Levenshtein over
trigram candidates). Scale horizontally:

```bash
docker compose up -d --scale wordswift=3
```

Put a load balancer in front. Note that **writes must go to a single instance**
— LevelDB takes an exclusive lock on its directory, so a shared volume across
replicas will fail. Use a read replica pattern or route writes to one node.

### Latency

- Exact keyword search: ~1–5 ms
- Fuzzy search (triggered when < 3 keyword hits): ~20–50 ms depending on vocabulary
- The cascade only runs fuzzy/prefix stages when keyword search is weak, so
  well-matched queries stay fast.

Disable expensive stages per-request when you don't need them:

```
/indexes/shop/search?q=jacket&typo_tolerance=false&prefix_search=false
```

### Scores are not normalized

Hit scores are **not** bounded to 0–1. BM25 is unbounded, and `hybrid=true` adds
a semantic component, so scores can exceed 1:

```
q=jacket              → 0.6195   (BM25 only)
q=waterprof jaket     → 0.991    (fuzzy path)
q=rain coat&hybrid=true → 1.293  (BM25×0.6 + semantic×0.4)
```

Treat scores as **relative ordering within one query**, never as absolute
confidence or as comparable across queries. Do not threshold on a fixed value
like `score > 0.5`.

---

## Next Steps

- **[Docker deployment](DOCKER.md)** — run WordSwift as a container.
- **[RAG & agents](RAG.md)** — expose your catalog to an LLM shopping assistant.

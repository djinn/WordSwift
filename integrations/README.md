# WordSwift Integrations

Ready-to-use adapters that connect WordSwift to ecommerce platforms and LLM
agent frameworks. Each integration is independent — use only what you need.

| Integration | Language | Status |
|-------------|----------|--------|
| [`common/`](common/) | Python | Shared HTTP client used by every Python integration |
| [`shopify/`](shopify/) | Python + JS | Tested against a live WordSwift instance |
| [`woocommerce/`](woocommerce/) | PHP | Syntax-validated (PHP 8.2); needs a WP site to run |
| [`bigcommerce/`](bigcommerce/) | Python + JS | Transform + pagination logic tested |
| [`magento/`](magento/) | PHP + XML | Syntax-validated; DI references verified |
| [`rag/`](rag/) | Python | Tested against a live instance, incl. citations |

> **Verification note:** the Python integrations were exercised against a running
> WordSwift server. The PHP modules cannot be run without a full Magento or
> WordPress install, so they are syntax-checked (`php -l`) and their class/DI
> references cross-checked, but not integration-tested. Treat them as a solid
> starting point rather than a drop-in certified module.

---

## Common client

All Python integrations share [`common/wordswift_client.py`](common/wordswift_client.py),
a dependency-free (stdlib only) HTTP client:

```python
from wordswift_client import WordSwiftClient, stringify_document

client = WordSwiftClient("http://localhost:7700")
client.create_index("my_products", dim=100)
client.add_documents("my_products", [
    stringify_document({"id": 1, "title": "Blue Jacket", "price": 99.00})
])
hits = client.search("my_products", "blue jaket", limit=10)
```

Two behaviours worth knowing, both documented inline:

- **`stringify_document()`** — WordSwift only indexes string fields
  (`isinstance(v, str)`), so integers, floats and lists are silently dropped
  unless coerced. The helper handles this and keeps `id` numeric.
- **`drop_semantic_only`** — the search API emits synthetic vocabulary hits
  (`_semantic: true`, negative ids) when it falls back to its semantic stage.
  The client filters these out by default because they are not real documents.

---

## Shopify

```
shopify/
├── sync.py                 # Full + single-product sync from the Admin API
├── webhook_server.py       # Real-time sync receiver + storefront search proxy
├── register_webhooks.py    # Registers products/create|update|delete
└── theme/
    └── wordswift-search.js # Storefront autocomplete widget
```

**Quick start:**

```bash
export SHOPIFY_STORE=your-store.myshopify.com
export SHOPIFY_TOKEN=shpat_xxxxxxxx          # Admin API token (sync only)
export WORDSWIFT_URL=http://localhost:7700

python3 sync.py --full                       # index every product
python3 sync.py --test-search "waterprof jaket"
```

**Real-time sync** needs a second secret — Shopify signs webhooks with the app's
**client secret**, not the `shpat_` token:

```bash
export SHOPIFY_WEBHOOK_SECRET=xxxxxxxx       # app's "API secret key"
export WEBHOOK_DESTINATION=https://your-host.example.com/webhooks/shopify

python3 webhook_server.py --port 8000        # in one terminal
python3 register_webhooks.py                 # once, to register the topics
```

The webhook server verifies `X-Shopify-Hmac-Sha256` and rejects bad, empty,
truncated or tampered signatures with `401`. It also exposes `POST /search`,
which filters tombstoned products — point the theme widget at this proxy rather
than at WordSwift directly so the port stays private.

---

## WooCommerce

```
woocommerce/
├── wordswift-search.php                  # Plugin bootstrap + search interception
└── includes/
    ├── class-wordswift-client.php        # WP HTTP API wrapper
    ├── class-wordswift-sync.php          # Product → document transform
    └── class-wordswift-admin.php         # Settings screen + resync UI
```

**Install:** copy the directory to `wp-content/plugins/wordswift-search/`,
activate it, then configure under **Settings → WordSwift**.

The plugin hooks `pre_get_posts` and rewrites the query with
`post__in` + `orderby => post__in` so WordSwift's BM25 ranking survives
WordPress's re-sorting. It also checks WooCommerce **catalog visibility** —
products set to "Hidden" or "Catalog only" are excluded, and tombstoned ids are
filtered out of results.

If WordSwift is unreachable, search silently falls back to native WooCommerce.

---

## BigCommerce

```
bigcommerce/
├── sync.py                        # Catalog v3 API sync
└── theme/
    └── wordswift-search.js        # Stencil plugin widget (ES module)
```

**Quick start:**

```bash
export BIGCOMMERCE_STORE_HASH=abc123def4
export BIGCOMMERCE_TOKEN=xxxxxxxx
export WORDSWIFT_URL=http://localhost:7700

python3 sync.py --full
python3 sync.py --full --dry-run    # preview the transformed document
```

Pagination handles all four link shapes the v3 API emits (absolute URL, path,
bare query string, relative segment). The Stencil widget registers as a theme
plugin via the `selectors` mechanism and calls `onReady()`.

---

## Magento / Adobe Commerce

```
magento/WordSwift/Search/
├── registration.php
├── etc/
│   ├── module.xml            # declaration + load sequence
│   ├── di.xml                # preferences, plugin, console commands
│   ├── config.xml            # default values
│   ├── acl.xml               # admin permission
│   ├── events.xml            # incremental indexing
│   └── adminhtml/system.xml  # Stores → Configuration screen
├── Api/ClientInterface.php
├── Model/{Client,Config,Sync}.php
├── Plugin/ItemCollectionProviderPlugin.php
├── Observer/ProductSaveObserver.php
└── Console/Command/{ResyncCommand,TestSearchCommand}.php
```

**Install:**

```bash
cp -r magento/WordSwift app/code/WordSwift
bin/magento module:enable WordSwift_Search
bin/magento setup:upgrade
bin/magento cache:flush
```

**Useful commands:**

```bash
bin/magento wordswift:resync --batch-size 100
bin/magento wordswift:search "waterprof jaket"
bin/magento wordswift:search --stats
```

The plugin targets `ItemCollectionProvider`, a version-stable seam, and
re-orders the collection with `FIELD(e.entity_id, ...)` to preserve WordSwift's
ranking. Ids are cast to `int` and validated `> 0` before interpolation.

---

## RAG & agents

```
rag/
├── wordswift_retriever.py   # Framework-neutral retriever + LangChain/LlamaIndex adapters
└── ecommerce_agent.py       # Agent tools + dispatch loop + offline demo
```

**Retrieval:**

```python
from wordswift_retriever import WordSwiftRetriever, grounded_answer

retriever = WordSwiftRetriever(index_name="knowledge_base", k=5)

for doc in retriever.retrieve("how do I get my money back"):
    print(doc.score, doc.title)
```

**Grounded answering with citation verification:**

```python
result = grounded_answer(retriever, "What is the return window?", call_llm=my_llm)

print(result["answer"])
print(result["sources"])          # resolved citations → real doc ids
print(result["bad_citations"])    # citations the model invented
print(result["insufficient"])     # True when the model returned the sentinel
```

**Agent tools:**

```python
from ecommerce_agent import EcommerceTools

tools = EcommerceTools(index="knowledge_base")
print(tools.search_knowledge("return policy"))
print(tools.find_similar("jacket"))
print(tools.word_analogy("shirt", "jeans", "dress"))
print(tools.index_stats())
print(tools.tool_schemas())       # OpenAI-style function schemas
```

Run the offline demo:

```bash
python3 ecommerce_agent.py --demo --url http://localhost:7700 --index knowledge_base
```

`word_analogy` requires a **trained embedding model** on the index; without one
it returns a clear message telling the agent to fall back to
`search_knowledge`, rather than crashing.

---

## Patterns shared by every integration

**1. The document schema.** All platforms map to the same shape — an integer
`id` plus string fields. See [docs/ECOMMERCE.md](../docs/ECOMMERCE.md#the-document-schema).

**2. Deletions need tombstones.** WordSwift's API can only delete a *whole
index* (`DELETE /indexes/{name}`) — there is no per-document delete. Each
integration therefore records deleted ids and filters them from results until
the next full resync. This is the single biggest limitation to plan around.

**3. Two-phase indexing.** Real-time webhooks/observers for freshness, plus a
scheduled full resync as a safety net against dropped events.

**4. Fail open.** If WordSwift is unreachable, every integration falls back to
the platform's native search rather than showing customers an error.

---

## Further reading

- [docs/DOCKER.md](../docs/DOCKER.md) — container deployment
- [docs/ECOMMERCE.md](../docs/ECOMMERCE.md) — platform integration patterns and scaling
- [docs/RAG.md](../docs/RAG.md) — retrieval cascade, chunking, citations, evaluation

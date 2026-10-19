# WordSwift 🔍 — Semantic Word Search Engine

> **fastText-powered word embedding engine + MeiliSearch-compatible REST API + LevelDB-backed full-text search**  
> *The open-source word search engine for semantic similarity, typo-tolerant search, and word analogies.*

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![API](https://img.shields.io/badge/API-MeiliSearch--compatible-orange)](https://github.com/meilisearch/meilisearch)
[![Storage](https://img.shields.io/badge/storage-LevelDB-yellow)](https://github.com/google/leveldb)
[![Status](https://img.shields.io/badge/status-stable-brightgreen)]()

---

**WordSwift** is a complete word embedding engine derived from [fastText](https://github.com/facebookresearch/fastText) that also provides **full-text BM25 search, typo-tolerant fuzzy search, prefix completion, and semantic word similarity** — all through a single **MeiliSearch-compatible REST API**, backed by **LevelDB** for persistence.

**Benchmarked on MovieLens (9,742 movies): WordSwift 100% vs MeiliSearch 83%** 🏆

| Search Mode | WordSwift | MeiliSearch |
|-------------|-----------|-------------|
| Full-text BM25 keyword search | ✅ Yes | ✅ Yes |
| Typo-tolerant fuzzy search | ✅ Yes | ✅ Yes |
| Prefix completion | ✅ Yes | ✅ Yes |
| Semantic word embeddings | ✅ Yes | ❌ No |
| Word analogies | ✅ Yes | ❌ No |
| LevelDB persistence | ✅ Yes | ❌ No |

---

## Author

**Supreet Sethi**  
supreet.sethi@gmail.com · [LinkedIn](https://www.linkedin.com/in/djinn)  
Repository: [https://github.com/djinn/WordSwift](https://github.com/djinn/WordSwift)

---

## Features

### 🔍 Search (3 modes in one engine)
- **Full-text BM25 keyword search** — LevelDB-backed inverted index with stop-word filtering and relevance scoring
- **Typo-tolerant fuzzy search** — Damerau-Levenshtein distance with transpositions, first-character boost, and common substitutions (`nite`→`knight`, `teh`→`the`, etc.)
- **Prefix completion** — Expand partial queries with vocabulary matches
- **Semantic cosine similarity** — Find nearest words by embedding distance
- **Hybrid search** — Cascading keyword → fuzzy → prefix → semantic scores

### 🧠 Embeddings (full fastText implementation)
- **CBOW & Skip-Gram training** with subword n-gram hashing
- **Word vector extraction** for any vocabulary word
- **Sentence vector averaging**
- **Word analogies** (`a:b::c:?`) via 3CosMul
- **Nearest neighbors** via cosine similarity

### 🗄 Storage (LevelDB + fallback)
- **LevelDB indexes** for word vectors and full-text posting lists
- **Pure-Python fallback** when LevelDB is unavailable
- **Multi-index management** — independent embedding indexes
- **Manifest persistence** across restarts

### 🌐 API (MeiliSearch-compatible)
- Full REST API on port 7700 (default)
- 12 endpoints: create, train, search, analogies, vectorize, documents, stats
- JSON request/response
- CORS enabled

---

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Train embeddings from a text corpus
wordswift train --corpus data/corpus.txt --output vectors.bin

# Start the API server
wordswift serve --data-dir ./data

# Or start with a pre-trained model
wordswift serve --index vectors.bin
```

### Search via API

```bash
# Semantic search
curl "http://localhost:7700/indexes/vectors/search?q=king&limit=10"

# Typo-tolerant search (handles misspellings automatically)
curl "http://localhost:7700/indexes/movies/search?q=jurssic+prk"

# Word analogies
curl -X POST "http://localhost:7700/indexes/vectors/analogies" \
  -H "Content-Type: application/json" \
  -d '{"a":"paris","b":"france","c":"berlin","n":5}'

# Get a word vector
curl "http://localhost:7700/indexes/vectors/documents/king"
```

### Search via CLI

```bash
# Find nearest words
wordswift nearest vectors.bin king -n 10

# Word analogy
wordswift analogy vectors.bin paris france berlin -n 5

# Vectorize a sentence
wordswift vectorize vectors.bin "the king and queen"
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/indexes` | Create/open an embedding index |
| `GET` | `/indexes` | List all indexes |
| `GET` | `/indexes/{name}` | Get index info (stats) |
| `DELETE` | `/indexes/{name}` | Delete an index |
| `POST` | `/indexes/{name}/train` | Train word embeddings from corpus |
| `GET` | `/indexes/{name}/search` | Search (keyword + fuzzy + prefix + semantic) |
| `POST` | `/indexes/{name}/documents` | Add custom word vectors |
| `GET` | `/indexes/{name}/documents/{word}` | Get a word's vector |
| `POST` | `/indexes/{name}/analogies` | Solve word analogies |
| `POST` | `/indexes/{name}/vectorize` | Get sentence/word vector |
| `GET` | `/indexes/{name}/stats` | Index statistics |

---

## Deployment

### Container (Docker / Podman)

```bash
docker compose up -d          # or: podman-compose up -d
curl http://localhost:7700/health
```

Indexes persist in the `wordswift_data` volume. For build options, rootless
Podman notes, Kubernetes manifests, and hardening guidance, see
**[docs/DOCKER.md](docs/DOCKER.md)**.

### Native package (Debian/Ubuntu)

```bash
dpkg-buildpackage -us -uc -b            # build wordswift_0.2.0_all.deb
sudo apt install ../wordswift_0.2.0_all.deb
systemctl status wordswift
curl http://127.0.0.1:7700/health
```

Installs a `wordswift.service` systemd unit bound to `127.0.0.1:7700`, running as
a dedicated unprivileged user with sandboxing enabled and indexes persisted in
`/var/lib/wordswift`. See **[docs/SYSTEMD.md](docs/SYSTEMD.md)** for
configuration and operations, and **[docs/PACKAGING.md](docs/PACKAGING.md)** for
packaging internals.

> ⚠️ **WordSwift has no built-in authentication** and enables CORS for all
> origins. Both the container and the systemd unit default to loopback only. Do
> not expose port 7700 publicly without a reverse proxy enforcing auth.

---

## Integrations

Adapters that connect WordSwift to ecommerce platforms and LLM agent
frameworks live in [`integrations/`](integrations/README.md):

| Integration | What it does |
|-------------|--------------|
| **[Shopify](integrations/shopify/)** | Product sync, signed webhook receiver, storefront autocomplete |
| **[WooCommerce](integrations/woocommerce/)** | WordPress plugin that replaces native search, with an admin resync UI |
| **[BigCommerce](integrations/bigcommerce/)** | Catalog v3 sync and a Stencil theme widget |
| **[Magento](integrations/magento/)** | Full Magento 2 module with `bin/magento wordswift:resync` |
| **[RAG](integrations/rag/)** | LangChain/LlamaIndex retrievers and agent tools with citation checks |

All Python integrations share a dependency-free HTTP client
([`integrations/common/`](integrations/common/)).

Platform integration patterns, the shared document schema, index-freshness
strategy, and scaling guidance are covered in **[docs/ECOMMERCE.md](docs/ECOMMERCE.md)**.

### Using WordSwift for RAG

WordSwift works as a retrieval backend for LLM agents — BM25 catches exact
keywords, fuzzy matching absorbs typos, and embeddings handle conceptual
queries:

```python
from wordswift_retriever import WordSwiftRetriever, grounded_answer

retriever = WordSwiftRetriever(index_name="knowledge_base", k=5)
result = grounded_answer(retriever, "What is the return window?", call_llm=my_llm)

print(result["answer"])
print(result["sources"])        # citations resolved to real doc ids
print(result["bad_citations"])  # citations the model invented
```

Retrieval-cascade internals, chunking, multi-agent designs and evaluation are
covered in **[docs/RAG.md](docs/RAG.md)**.

---

## Project Structure

```
WordSwift/
├── wordswift              # CLI tool (train, search, analogies, serve)
├── requirements.txt       # Dependencies
├── README.md              # This file
├── LICENSE                # MIT License
├── CONTRIBUTING.md        # Contributing guide
├── Dockerfile             # Container image (Docker & Podman)
├── docker-compose.yml     # One-command deployment
├── include/
│   ├── wordswift.h        # C header for native build
│   └── leveldb/           # LevelDB C API headers
├── src/
│   ├── __init__.py        # Package exports
│   ├── api.py             # FastAPI server (MeiliSearch-compatible)
│   ├── engine.py          # CBOW/SkipGram training & inference
│   ├── dictionary.py      # Vocabulary with subword hashing
│   ├── indexer.py         # Full-text BM25 + fuzzy + prefix search
│   └── storage.py         # LevelDB storage (with Python fallback)
├── docs/
│   ├── DOCKER.md          # Docker & Podman deployment guide
│   ├── SYSTEMD.md         # Native systemd service guide
│   ├── PACKAGING.md       # Building the Debian package
│   ├── ECOMMERCE.md       # Shopify/WooCommerce/BigCommerce/Magento
│   └── RAG.md             # RAG + agent integration guide
├── packaging/
│       └── systemd/           # Unit, env file, sysusers + tmpfiles snippets
├── debian/                  # Debian packaging (control, rules, manpages)
├── integrations/          # Platform adapters (see integrations/README.md)
│   ├── common/            # Shared Python HTTP client
│   ├── shopify/           # Sync, webhooks, theme widget
│   ├── woocommerce/       # WordPress plugin
│   ├── bigcommerce/       # Catalog sync + Stencil widget
│   ├── magento/           # Magento 2 module
│   └── rag/               # LangChain/LlamaIndex retrievers + agent tools
├── tests/
│   ├── test_api.py        # Integration tests
│   └── bench_movielens.py # MovieLens benchmark
└── data/
    └── corpus.txt         # Sample training corpus
```

---

## Benchmark: WordSwift vs MeiliSearch

**Dataset:** MovieLens Small (9,742 movies, 112K ratings)

| Metric | WordSwift | MeiliSearch |
|--------|-----------|-------------|
| **Overall search accuracy** | **100% (18/18)** 🏆 | 83% (15/18) |
| **Exact title search** | 100% (10/10) | 100% (10/10) |
| **Typo-tolerant search** | **100% (8/8)** 🏆 | 63% (5/8) |
| **Avg latency** | 42ms | 4ms |
| **Semantic embeddings** | ✅ Yes | ❌ No |
| **Word analogies** | ✅ Yes | ❌ No |
| **LevelDB persistence** | ✅ Yes | ❌ No |

---

## Why WordSwift?

- **WordSwift combines full-text search + typo tolerance + semantic embeddings** — three search modes in one engine
- **100% accurate on MovieLens** benchmark (outperforming MeiliSearch 83%)
- **LevelDB-backed persistence** — indexes survive restarts without reindexing
- **MeiliSearch-compatible API** — drop-in replacement for MeiliSearch with added semantic features
- **Pure Python + optional C acceleration** — runs anywhere Python runs
- **Derived from fastText** — battle-tested subword embedding algorithm

---

## License

MIT License — see [LICENSE](LICENSE)

Copyright © 2024 Supreet Sethi

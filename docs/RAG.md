# WordSwift as a RAG Backend

WordSwift is a **retriever**, not an LLM. In a RAG (Retrieval-Augmented
Generation) pipeline it does the *R*: given a user query, it returns the most
relevant documents, which you then stuff into a prompt for an LLM to answer
from.

Its advantage over a plain vector database is that it runs **three retrieval
strategies in one cascade**, so it handles query types that defeat either
approach alone:

| Query type | Example | What catches it |
|------------|---------|-----------------|
| Exact keyword | "return policy" | BM25 full-text |
| Misspelled | "retun policty" | Trigram + Damerau-Levenshtein |
| Partial | "poli" | Prefix completion |
| Conceptual | "how do I get my money back" | Semantic embeddings |

> **Honest caveat:** WordSwift's embeddings are fastText-style *word* vectors
> trained on your corpus — they are not sentence-transformer grade. The BM25 and
> fuzzy stages carry most of the retrieval quality. For heavy semantic RAG,
> train on a corpus that resembles your query distribution, and treat the
> semantic stage as a fallback rather than the primary signal.

---

## Table of Contents

- [The Retrieval Cascade](#the-retrieval-cascade)
- [Basic RAG Loop](#basic-rag-loop)
- [LangChain Integration](#langchain-integration)
- [LlamaIndex Integration](#llamaindex-integration)
- [Agent Tools](#agent-tools)
- [Multi-Agent Architecture](#multi-agent-architecture)
- [Chunking Strategy](#chunking-strategy)
- [Citations and Grounding](#citations-and-grounding)
- [Evaluating Retrieval Quality](#evaluating-retrieval-quality)

---

## The Retrieval Cascade

Understanding the cascade lets you tune it. From `src/api.py`:

```
query
  │
  ├─ Stage 1: BM25 keyword search        (always runs, limit+offset+20 candidates)
  │
  ├─ Stage 2: fuzzy search               (only if < 3 hits AND typo_tolerance=true)
  │            results downweighted ×0.8
  │
  ├─ Stage 3: prefix completion          (only if < 3 hits AND prefix_search=true)
  │            results downweighted ×0.7
  │
  ├─ Stage 4: semantic embedding         (only if < 5 hits AND model is trained)
  │            results downweighted ×0.3, synthetic negative doc_ids
  │
  └─ Stage 5: hybrid rescore             (only if hybrid=true)
               combined = bm25×0.6 + semantic×0.4
  │
  └─ sort by score desc → offset/limit → hits
```

Two practical consequences:

1. **Stages 2–4 are conditional.** If BM25 already found 3+ hits, fuzzy and
   prefix never run. Good queries stay fast (~1–5 ms).
2. **Stage 4 emits synthetic documents** with negative `id` values and a
   `"_semantic": true` marker. Filter those out before citing sources — they
   represent vocabulary words, not real documents.

```python
real_hits = [h for h in response["hits"] if not h.get("_semantic")]
```

---

## Basic RAG Loop

A minimal, dependency-free RAG loop over any LLM with an HTTP API:

```python
import json
import urllib.request

WORDSWIFT = "http://localhost:7700"
INDEX = "knowledge_base"


def retrieve(query: str, k: int = 5) -> list[dict]:
    """Retrieve top-k documents from WordSwift."""
    params = urllib.parse.urlencode({
        "q": query,
        "limit": k,
        "typo_tolerance": "true",
        "hybrid": "true",
    })
    url = f"{WORDSWIFT}/indexes/{INDEX}/search?{params}"
    with urllib.request.urlopen(url) as resp:
        data = json.load(resp)

    # Drop synthetic semantic-fallback entries
    return [h for h in data["hits"] if not h.get("_semantic")]


def build_context(hits: list[dict]) -> str:
    """Format retrieved docs as a numbered, citable context block."""
    blocks = []
    for i, h in enumerate(hits, 1):
        title = h.get("title", "Untitled")
        body = h.get("description") or h.get("content") or ""
        blocks.append(f"[{i}] {title}\n{body}")
    return "\n\n".join(blocks)


def answer(question: str) -> dict:
    hits = retrieve(question)
    context = build_context(hits)

    prompt = f"""Answer the question using ONLY the context below.
Cite sources inline as [n]. If the context is insufficient, say so.

CONTEXT:
{context}

QUESTION: {question}

ANSWER:"""

    # Swap in your LLM call of choice
    text = call_llm(prompt)
    return {"answer": text, "sources": hits}
```

The key discipline: **pass the retrieved text into the prompt and instruct the
model to cite it.** WordSwift gives you `id` and `title` per hit, so citations
map cleanly back to source documents.

---

## LangChain Integration

A `BaseRetriever` subclass makes WordSwift a first-class LangChain component,
usable anywhere LangChain expects a retriever — chains, agents, and RAG
templates.

See [`integrations/rag/wordswift_retriever.py`](../integrations/rag/wordswift_retriever.py).

```python
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.callbacks import CallbackManagerForRetrieverRun
import requests
from typing import List


class WordSwiftRetriever(BaseRetriever):
    """LangChain retriever backed by WordSwift's hybrid search."""

    wordswift_url: str = "http://localhost:7700"
    index_name: str = "default"
    k: int = 5
    hybrid: bool = True
    typo_tolerance: bool = True
    prefix_search: bool = True
    drop_semantic_only: bool = True
    timeout: float = 10.0

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        resp = requests.get(
            f"{self.wordswift_url}/indexes/{self.index_name}/search",
            params={
                "q": query,
                "limit": self.k,
                "hybrid": str(self.hybrid).lower(),
                "typo_tolerance": str(self.typo_tolerance).lower(),
                "prefix_search": str(self.prefix_search).lower(),
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        hits = resp.json()["hits"]

        docs = []
        for h in hits:
            if self.drop_semantic_only and h.get("_semantic"):
                continue  # synthetic vocabulary hit, not a real document
            content = h.get("description") or h.get("content") or h.get("title", "")
            docs.append(Document(
                page_content=f"{h.get('title', '')}\n{content}".strip(),
                metadata={
                    "id": h.get("id"),
                    "score": h.get("score"),
                    "source": self.index_name,
                    **{k: v for k, v in h.items()
                       if k not in ("id", "score", "title", "description",
                                    "content", "positions")},
                },
            ))
        return docs
```

### Wire it into a RAG chain

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

retriever = WordSwiftRetriever(index_name="knowledge_base", k=5)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

prompt = ChatPromptTemplate.from_template("""Answer using only the context. Cite as [n].

CONTEXT:
{context}

QUESTION: {question}""")


def format_docs(docs):
    return "\n\n".join(
        f"[{i}] {d.page_content}" for i, d in enumerate(docs, 1)
    )


chain = (
    {"context": retriever | format_docs, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)

print(chain.invoke("How do I return an item?"))
```

---

## LlamaIndex Integration

LlamaIndex uses a `BaseRetriever` with a different signature. Wrap the same
HTTP call:

```python
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.schema import NodeWithScore, TextNode
import requests


class WordSwiftRetriever(BaseRetriever):
    def __init__(self, url="http://localhost:7700", index_name="default",
                 k=5, **kwargs):
        self.url = url
        self.index_name = index_name
        self.k = k
        super().__init__(**kwargs)

    def _retrieve(self, query_bundle) -> list[NodeWithScore]:
        resp = requests.get(
            f"{self.url}/indexes/{self.index_name}/search",
            params={"q": query_bundle.query_str, "limit": self.k,
                    "hybrid": "true", "typo_tolerance": "true"},
            timeout=10,
        )
        resp.raise_for_status()

        nodes = []
        for h in resp.json()["hits"]:
            if h.get("_semantic"):
                continue
            text = h.get("description") or h.get("content") or h.get("title", "")
            nodes.append(NodeWithScore(
                node=TextNode(
                    text=f"{h.get('title', '')}\n{text}".strip(),
                    metadata={"id": h.get("id"), "source": self.index_name},
                    id_=str(h.get("id")),
                ),
                score=float(h.get("score", 0.0)),
            ))
        return nodes
```

```python
from llama_index.core import QueryEngine
from llama_index.core.response_synthesizers import get_response_synthesizer

query_engine = QueryEngine(
    retriever=WordSwiftRetriever(index_name="knowledge_base"),
    response_synthesizer=get_response_synthesizer(response_mode="compact"),
)
print(query_engine.query("What is the warranty period?"))
```

---

## Agent Tools

An **agent** decides *when* and *how* to call retrieval. Expose WordSwift as
tools and let the model choose between keyword lookup, semantic search, and
analogy reasoning.

Three tools map naturally onto WordSwift's three strengths:

| Tool | Backing endpoint | When the agent should use it |
|------|------------------|------------------------------|
| `search_knowledge` | `GET /indexes/{i}/search` | Find documents matching a query |
| `find_similar` | `GET /indexes/{i}/search?hybrid=true` | "What else is like X?" |
| `word_analogy` | `POST /indexes/{i}/analogies` | Relational reasoning (`a:b::c:?`) |

See [`integrations/rag/ecommerce_agent.py`](../integrations/rag/ecommerce_agent.py).

```python
import json
import requests

WORDSWIFT = "http://localhost:7700"


def search_knowledge(query: str, index: str = "knowledge_base", k: int = 5) -> str:
    """Search documents by keyword, meaning, or approximate spelling."""
    r = requests.get(
        f"{WORDSWIFT}/indexes/{index}/search",
        params={"q": query, "limit": k, "typo_tolerance": "true", "hybrid": "true"},
        timeout=10,
    )
    r.raise_for_status()
    hits = [h for h in r.json()["hits"] if not h.get("_semantic")]
    if not hits:
        return "No matching documents found."
    return "\n\n".join(
        f"[{i}] {h.get('title')} (id={h.get('id')}, score={h.get('score')})\n"
        f"{(h.get('description') or h.get('content') or '')[:600]}"
        for i, h in enumerate(hits, 1)
    )


def find_similar(item: str, index: str = "knowledge_base", k: int = 5) -> str:
    """Find items semantically similar to a given item name."""
    r = requests.get(
        f"{WORDSWIFT}/indexes/{index}/search",
        params={"q": item, "limit": k, "hybrid": "true"},
        timeout=10,
    )
    r.raise_for_status()
    hits = [h for h in r.json()["hits"] if not h.get("_semantic")]
    return "\n".join(f"- {h.get('title')} (score={h.get('score')})" for h in hits)


def word_analogy(a: str, b: str, c: str, index: str = "knowledge_base", n: int = 5) -> str:
    """Relational analogy: a is to b as c is to what?"""
    r = requests.post(
        f"{WORDSWIFT}/indexes/{index}/analogies",
        json={"a": a, "b": b, "c": c, "n": n},
        timeout=10,
    )
    if r.status_code == 404:
        return "Analogy unavailable — a word is missing from the vocabulary."
    r.raise_for_status()
    return "\n".join(
        f"- {h['word']} (score={h['score']:.3f})" for h in r.json()["hits"]
    )
```

### LangGraph agent

```python
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

@tool
def search_knowledge_tool(query: str) -> str:
    """Search the knowledge base by keyword, meaning, or approximate spelling.
    Use for any factual lookup."""
    return search_knowledge(query)

@tool
def find_similar_tool(item: str) -> str:
    """Find items semantically similar to a given item."""
    return find_similar(item)

@tool
def analogy_tool(a: str, b: str, c: str) -> str:
    """Solve a:b::c:? — relational reasoning between concepts."""
    return word_analogy(a, b, c)

agent = create_react_agent(
    model=ChatOpenAI(model="gpt-4o-mini", temperature=0),
    tools=[search_knowledge_tool, find_similar_tool, analogy_tool],
    prompt=(
        "You are a support assistant. ALWAYS search the knowledge base before "
        "answering. Cite document ids. If search returns nothing, say so rather "
        "than guessing."
    ),
)

result = agent.invoke({"messages": [("user", "What's your return window?")]})
print(result["messages"][-1].content)
```

### Why give the agent multiple retrieval tools?

A single `search` tool forces the model to phrase every query as keywords.
Splitting them lets the agent reason about *retrieval strategy*:

- "What's the return policy?" → `search_knowledge`
- "I bought the Torrentshell, what else goes with it?" → `find_similar`
- "shirt is to jeans as dress is to what?" → `analogy_tool`

> **Caveat on analogies:** the `/analogies` endpoint requires a *trained*
> embedding model (`engine.vectors` is not None). On an index that only has
> documents added — no `POST /train` — it returns 404. Train on your corpus
> before exposing this tool, and handle the 404 gracefully as shown above.

---

## Multi-Agent Architecture

For larger systems, separate concerns across agents that share one retrieval
backend. Each agent gets its own **index**, so relevance tuning and vocabulary
stay domain-specific:

```
                    ┌──────────────────┐
                    │   Supervisor     │
                    │ (routes intent)  │
                    └────────┬─────────┘
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       ┌───────────┐  ┌───────────┐  ┌───────────┐
       │ Product   │  │ Order     │  │ Support   │
       │ Agent     │  │ Agent     │  │ Agent     │
       └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
             │              │              │
             ▼              ▼              ▼
       index: products index: orders index: help_docs
             └──────────────┴──────────────┘
                            │
                   WordSwift (:7700)
```

### Why one index per agent?

WordSwift's BM25 statistics (`df`, `avg_dl`) are computed **per index**. Mixing
order records with product descriptions pollutes term frequency — "return" would
rank differently if 40% of the corpus is policy text versus transaction logs.
Separate indexes keep IDF meaningful.

### Supervisor routing

```python
INTENTS = {
    "product":  ("products",  "Find items, compare products, check stock"),
    "order":    ("orders",    "Order status, tracking, invoices"),
    "support":  ("help_docs", "Policies, returns, how-to questions"),
}

def route(question: str) -> str:
    """Let the LLM pick an index, then retrieve from it."""
    intent = classify_intent(question)          # your LLM call
    index = INTENTS[intent][0]
    return search_knowledge(question, index=index)
```

### Cross-index search

Sometimes a question spans domains ("I ordered the Torrentshell — can I return
it?"). Fan out and merge:

```python
def multi_index_search(query: str, indexes: list[str], k: int = 3):
    results = []
    for idx in indexes:
        hits = requests.get(
            f"{WORDSWIFT}/indexes/{idx}/search",
            params={"q": query, "limit": k, "hybrid": "true"},
            timeout=10,
        ).json()["hits"]
        for h in hits:
            if not h.get("_semantic"):
                h["_index"] = idx
                results.append(h)

    results.sort(key=lambda x: -x.get("score", 0))
    return results[:k]
```

> Scores are **not directly comparable across indexes** — BM25 depends on each
> index's document count and average length. Re-ranking merged results by raw
> score is a rough heuristic. For production, normalize per index (divide by
> that index's max score) or add a cross-encoder reranker.

---

## Chunking Strategy

WordSwift indexes **whole documents** and scores them with BM25 over all their
string fields. It has no built-in chunking, so chunking is your job before
indexing.

### Recommended: overlap-aware chunking

```python
def chunk_text(text: str, max_words: int = 200, overlap: int = 40) -> list[str]:
    """Split into overlapping word windows."""
    words = text.split()
    if len(words) <= max_words:
        return [text]

    chunks, start = [], 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - overlap        # step forward, keeping `overlap` words
    return chunks
```

Index each chunk as its own document, carrying parent metadata for citation:

```python
docs = []
for i, chunk in enumerate(chunks):
    docs.append({
        "id": f"{parent_id}_{i}",          # must coerce to int — see note
        "title": f"{parent_title} (part {i + 1})",
        "content": chunk,
        "source_id": str(parent_id),       # for grouping hits back together
        "source_url": parent_url,
    })
```

> ⚠️ **The `id` field is coerced to `int`.** Looking at `add_documents` in
> `src/api.py`: if `id` is a non-numeric string, it falls back to an
> auto-incrementing counter — so `"policy_3"` becomes an arbitrary integer and
> you lose the mapping. **Use integer IDs and store your string key in a
> separate metadata field.**

### Chunk size guidance

| Content type | Chunk size | Overlap |
|--------------|-----------|---------|
| FAQ / support articles | 150–250 words | 30 |
| Long-form docs / manuals | 300–500 words | 50 |
| Product descriptions | Don't chunk | — |
| Legal / policy text | By section heading | 0 |

Product descriptions are already short and self-contained — chunking them
fragments the title from its attributes and hurts BM25 ranking.

---

## Citations and Grounding

The value of RAG over raw LLM calls is **verifiability**. Wire citations
end-to-end:

```python
def grounded_answer(question: str) -> dict:
    hits = retrieve(question, k=5)

    context = "\n\n".join(
        f"[{i}] {h.get('title')} (doc {h.get('id')})\n"
        f"{(h.get('description') or h.get('content') or '')[:800]}"
        for i, h in enumerate(hits, 1)
    )

    answer = call_llm(
        f"Answer using ONLY the numbered context. Cite every claim as [n]. "
        f"If the context doesn't answer the question, reply exactly: "
        f"INSUFFICIENT_CONTEXT\n\nCONTEXT:\n{context}\n\nQUESTION: {question}"
    )

    # Map cited numbers back to real sources
    cited = [int(n) for n in re.findall(r"\[(\d+)\]", answer)]
    sources = [
        {"index": i, "id": hits[i - 1].get("id"),
         "title": hits[i - 1].get("title"),
         "url": hits[i - 1].get("source_url")}
        for i in sorted(set(cited)) if 1 <= i <= len(hits)
    ]

    return {"answer": answer, "sources": sources, "retrieved": len(hits)}
```

Two safeguards that matter in practice:

1. **`INSUFFICIENT_CONTEXT` sentinel.** Without an explicit escape hatch, models
   will happily answer from parametric memory while appearing grounded.
2. **Verify citations resolve.** If the model cites `[7]` but you only retrieved
   5 documents, that's a hallucinated citation — catch it and retry or flag.

---

## Evaluating Retrieval Quality

You cannot tune what you don't measure. Build a small labelled set and track
retrieval metrics independently of the LLM.

### A minimal evaluation harness

```python
# Build once: (query, relevant_doc_ids) pairs
EVAL_SET = [
    ("waterproof jacket",        [1, 3]),
    ("waterprof jaket",          [1, 3]),   # typo robustness
    ("return policy",            [42, 43]),
    ("how do i get a refund",    [42]),     # semantic
]

def evaluate(index: str) -> dict:
    metrics = {"recall@5": [], "mrr": [], "p50_latency_ms": []}

    for query, relevant in EVAL_SET:
        t0 = time.perf_counter()
        hits = retrieve(query, k=5)
        metrics["p50_latency_ms"].append((time.perf_counter() - t0) * 1000)

        retrieved_ids = [h.get("id") for h in hits]
        found = [i for i, d in enumerate(retrieved_ids) if d in relevant]

        # Recall@5
        metrics["recall@5"].append(len(set(found)) / len(relevant))
        # MRR — reciprocal rank of first relevant hit
        metrics["mrr"].append(1.0 / (found[0] + 1) if found else 0.0)

    return {k: sum(v) / len(v) for k, v in metrics.items()}
```

### What to tune

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Typos not caught | `typo_tolerance=false`, or ≥3 BM25 hits already | Cascade skips stage 2 by design — check hit counts |
| Irrelevant top hit | Stop words dominating | `tokenize()` already strips 26 stop words; extend `_STOP_WORDS` in `src/indexer.py` |
| Semantic stage noisy | Model untrained or trained on unrelated corpus | Run `POST /train` on domain text |
| Slow queries | Large vocabulary × fuzzy matching | `typo_tolerance=false` skips Levenshtein |
| Duplicate results | Same doc chunked without dedup | Group by `source_id` before rendering |

### Baseline reference

The repo includes a MovieLens benchmark (`tests/bench_movielens.py`) reporting
**100% (18/18)** overall vs. MeiliSearch's 83%, and **100% (8/8)** on
typo-tolerant queries. Note it has **hardcoded paths** and needs a running
MeiliSearch to produce the comparison half:

```bash
# It reads /home/ubuntu/benchmarks/ml-latest-small/movies.csv — edit the paths
# at the top of the file to point at your own copy of the dataset.
docker start meilisearch          # for the MeiliSearch side of the comparison
python3 tests/bench_movielens.py
```

If MeiliSearch isn't running, the script prints WordSwift's numbers and skips
the comparison — still useful as a retrieval sanity check.

---

## Next Steps

- **[Docker deployment](DOCKER.md)** — run WordSwift as a container.
- **[Ecommerce integration](ECOMMERCE.md)** — connect a product catalog.
- [`integrations/rag/`](../integrations/rag/) — runnable retriever and agent code.

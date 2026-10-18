"""
WordSwift — FastAPI server (MeiliSearch-compatible API) with full-text + typo search.

Adds to the embedding search engine:
  1. Full-text keyword search via inverted index (LevelDB-backed)
  2. Typo-tolerant search via trigram + Levenshtein
  3. Prefix completion search
  4. Hybrid search combining full-text + semantic scores

Author: Supreet Sethi <supreet.sethi@gmail.com>
Repository: https://github.com/djinn/WordSwift
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .engine import WordSwiftEngine
from .storage import pack_f32s, unpack_f32s
from .indexer import (InvertedIndex, tokenize, normalize, levenshtein,
                       get_ngrams)


# ======================================================================
# Application
# ======================================================================

app = FastAPI(
    title="WordSwift",
    version="0.2.0",
    description="Word search engine with full-text, typo-tolerant, and semantic search",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ======================================================================
# Index Registry
# ======================================================================

class IndexRegistry:
    """Manages embedding indexes (WordSwiftEngine) AND full-text indexes (InvertedIndex)."""

    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.indexes: Dict[str, WordSwiftEngine] = {}
        self.ft_indexes: Dict[str, InvertedIndex] = {}
        self._doc_counter: Dict[str, int] = {}
        self._load_manifest()

    def _manifest_path(self) -> Path:
        return self.data_dir / "manifest.json"

    def _load_manifest(self) -> None:
        path = self._manifest_path()
        if path.exists():
            with open(path) as f:
                manifest = json.load(f)
            for entry in manifest:
                name = entry["name"]
                model_path = entry.get("model_path")
                db_path = entry.get("db_path")
                ft_path = entry.get("ft_path")

                engine = WordSwiftEngine()
                if model_path and Path(model_path).exists():
                    try:
                        engine.load(model_path)
                    except Exception as e:
                        print(f"  Warning: failed to load '{name}': {e}", file=sys.stderr)
                if db_path:
                    engine.open_index(db_path)

                if ft_path:
                    try:
                        self.ft_indexes[name] = InvertedIndex(ft_path)
                    except Exception as e:
                        print(f"  Warning: failed to open FT index '{name}': {e}", file=sys.stderr)

                self.indexes[name] = engine

    def _save_manifest(self) -> None:
        manifest = []
        for name in self.indexes:
            entry = {"name": name}
            model_path = self.data_dir / f"{name}.ws"
            if model_path.exists():
                entry["model_path"] = str(model_path)
            db_path = self.data_dir / f"{name}_index"
            if db_path.exists():
                entry["db_path"] = str(db_path)
            ft_path = self.data_dir / f"{name}_ft"
            if ft_path.exists():
                entry["ft_path"] = str(ft_path)
            manifest.append(entry)
        with open(self._manifest_path(), "w") as f:
            json.dump(manifest, f, indent=2)

    def create(self, name: str, dim: int = 100) -> WordSwiftEngine:
        if name in self.indexes:
            raise ValueError(f"Index already exists: {name}")
        engine = WordSwiftEngine(dim=dim)
        db_path = self.data_dir / f"{name}_index"
        ft_path = self.data_dir / f"{name}_ft"
        engine.open_index(str(db_path))
        self.ft_indexes[name] = InvertedIndex(str(ft_path))
        self.indexes[name] = engine
        self._doc_counter[name] = 0
        self._save_manifest()
        return engine

    def get(self, name: str) -> Optional[WordSwiftEngine]:
        return self.indexes.get(name)

    def get_ft(self, name: str) -> Optional[InvertedIndex]:
        return self.ft_indexes.get(name)

    def add_documents(self, name: str, documents: List[Dict]) -> int:
        """Add documents to full-text index."""
        if name not in self.ft_indexes:
            ft_path = self.data_dir / f"{name}_ft"
            self.ft_indexes[name] = InvertedIndex(str(ft_path))

        ft = self.ft_indexes[name]
        engine = self.indexes.get(name)
        count = 0

        for doc in documents:
            doc_id = doc.get("id", self._doc_counter.get(name, 0))
            if isinstance(doc_id, str) and not doc_id.isdigit():
                doc_id = self._doc_counter.get(name, 0)
            doc_id = int(doc_id)
            self._doc_counter[name] = max(self._doc_counter.get(name, 0), doc_id + 1)

            text_fields = {k: v for k, v in doc.items() if isinstance(v, str)}
            ft.add_document(doc_id, text_fields)
            count += 1

        return count

    def delete(self, name: str) -> bool:
        engine = self.indexes.pop(name, None)
        ft = self.ft_indexes.pop(name, None)
        if ft:
            ft.close()
        if engine:
            engine.close_index()
            for suffix in [".ws", "_index", "_ft"]:
                p = self.data_dir / f"{name}{suffix}"
                if p.exists():
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink(missing_ok=True)
            self._save_manifest()
            return True
        return False

    def list_indexes(self) -> List[Dict]:
        result = []
        for name, engine in self.indexes.items():
            info = {"uid": name, "name": name}
            if engine.vectors is not None:
                info.update(engine.get_stats())
            else:
                info["vocab_size"] = 0
                info["dimension"] = engine.dim
            ft = self.ft_indexes.get(name)
            info["full_text_docs"] = len(ft) if ft else 0
            result.append(info)
        return result


# The data directory is resolved from the environment so that it survives
# uvicorn's re-import of this module when started via the "src.api:app"
# import string (used by --reload and multi-worker mode).
DEFAULT_DATA_DIR = os.environ.get("WORDSWIFT_DATA_DIR", "data")

registry = IndexRegistry(DEFAULT_DATA_DIR)


# ======================================================================
# Health
# ======================================================================

@app.get("/health")
async def health():
    return {"status": "available", "indexes": len(registry.indexes)}


# ======================================================================
# Index Management
# ======================================================================

@app.post("/indexes")
async def create_index(
    uid: str = Body(..., description="Index UID"),
    primaryKey: Optional[str] = Body(None),
    dim: int = Body(100),
):
    """Create a new embedding + full-text index."""
    try:
        engine = registry.create(uid, dim=dim)
        return {
            "uid": uid,
            "primaryKey": primaryKey,
            "dimension": dim,
            "vocab_size": engine.vocab_size,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/indexes")
async def list_indexes():
    """List all indexes."""
    return {"results": registry.list_indexes(), "total": len(registry.indexes)}


@app.get("/indexes/{index_uid}")
async def get_index(index_uid: str):
    """Get information about a specific index."""
    engine = registry.get(index_uid)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Index not found: {index_uid}")
    info = {"uid": index_uid, "name": index_uid}
    if engine.vectors is not None:
        info.update(engine.get_stats())
    ft = registry.get_ft(index_uid)
    info["full_text_docs"] = len(ft) if ft else 0
    return info


@app.delete("/indexes/{index_uid}")
async def delete_index(index_uid: str):
    """Delete an index."""
    if registry.delete(index_uid):
        return {"taskUid": str(uuid.uuid4()), "status": "succeeded"}
    raise HTTPException(status_code=404, detail=f"Index not found: {index_uid}")


@app.get("/indexes/{index_uid}/stats")
async def get_index_stats(index_uid: str):
    """Get statistics for an index."""
    engine = registry.get(index_uid)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Index not found: {index_uid}")
    ft = registry.get_ft(index_uid)
    return {
        "numberOfDocuments": engine.vocab_size,
        "databaseSize": engine.index_size if engine.ldb else 0,
        "dimension": engine.dim,
        "fullTextDocuments": len(ft) if ft else 0,
        "lastUpdate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ======================================================================
# Training
# ======================================================================

@app.post("/indexes/{index_uid}/train")
async def train_index(
    index_uid: str,
    corpus: str = Body(..., description="Path to training corpus file"),
    dim: int = Body(100),
    ws: int = Body(5),
    epoch: int = Body(5),
    min_count: int = Body(5),
    lr: float = Body(0.05),
):
    """Train word embeddings from a corpus file."""
    engine = registry.get(index_uid)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Index not found: {index_uid}")

    try:
        engine.configure(dim=dim, ws=ws, epoch=epoch, min_count=min_count, lr=lr)
        engine.train(corpus)
        model_path = registry.data_dir / f"{index_uid}.ws"
        engine.save(str(model_path))
        engine.index_all()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "taskUid": str(uuid.uuid4()),
        "status": "succeeded",
        "vocab_size": engine.vocab_size,
    }


# ======================================================================
# Documents (full CRUD for full-text index)
# ======================================================================

@app.post("/indexes/{index_uid}/documents")
async def add_documents(index_uid: str, documents: List[Dict] = Body(...)):
    """Add documents to the full-text search index.

    Each document should have an 'id' field and text fields to index.
    Also indexes word vectors when an embedding model is present.
    """
    engine = registry.get(index_uid)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Index not found: {index_uid}")

    count = registry.add_documents(index_uid, documents)

    # Also store word-level vectors
    vec_count = 0
    if engine and engine.vectors is not None:
        for doc in documents:
            for word in tokenize(str(doc.get("title", ""))):
                try:
                    vec = engine.get_word_vector(word)
                    engine.index_word(word)
                    vec_count += 1
                except KeyError:
                    continue

    return {
        "taskUid": str(uuid.uuid4()),
        "status": "succeeded",
        "documentsCount": count,
        "vectorsIndexed": vec_count,
    }


# ======================================================================
# SEARCH — Full-text + Typo-tolerant + Semantic (Hybrid)
# ======================================================================

@app.get("/indexes/{index_uid}/search")
async def search(
    index_uid: str,
    q: str = Query(..., description="Search query"),
    limit: int = Query(10, description="Maximum number of results"),
    offset: int = Query(0, description="Result offset"),
    typo_tolerance: bool = Query(True, description="Enable typo-tolerant search"),
    prefix_search: bool = Query(True, description="Enable prefix completion"),
    hybrid: bool = Query(False, description="Combine semantic + full-text scores"),
):
    """Search across documents with multiple strategies.

    Uses a cascade:
      1. Full-text keyword search (BM25) via inverted index
      2. Typo-tolerant fallback (trigram + Levenshtein)
      3. Prefix completion expansion
      4. Optionally hybridized with semantic embedding scores
    """
    engine = registry.get(index_uid)
    ft = registry.get_ft(index_uid)

    if not engine:
        raise HTTPException(status_code=404, detail=f"Index not found: {index_uid}")

    # ---- Stage 1: Full-text keyword search ----
    hits_raw = []
    if ft is not None:
        try:
            hits_raw = ft.search_keyword(q, limit=limit + offset + 20)
        except Exception:
            hits_raw = []

    # ---- Stage 2: Typo-tolerant search (if no results or few) ----
    if len(hits_raw) < 3 and typo_tolerance and ft is not None:
        try:
            fuzzy_hits = ft.search_fuzzy(q, max_edits=2,
                                         limit=limit + offset + 20)
            # Merge, avoiding duplicates
            seen_ids = set(doc_id for doc_id, _, _ in hits_raw)
            for doc_id, score, data in fuzzy_hits:
                if doc_id not in seen_ids:
                    hits_raw.append((doc_id, score * 0.8, data))
                    seen_ids.add(doc_id)
        except Exception:
            pass

    # ---- Stage 3: Prefix expansion (if still few results) ----
    if len(hits_raw) < 3 and prefix_search and ft is not None:
        try:
            prefix_hits = ft.search_prefix(q, limit=limit + offset + 20)
            seen_ids = set(doc_id for doc_id, _, _ in hits_raw)
            for doc_id, score, data in prefix_hits:
                if doc_id not in seen_ids:
                    hits_raw.append((doc_id, score * 0.7, data))
                    seen_ids.add(doc_id)
        except Exception:
            pass

    # ---- Stage 4: Semantic embedding as fallback ----
    if len(hits_raw) < 5 and engine.vectors is not None:
        try:
            query_words = tokenize(q)
            semantic_results = []
            for word in query_words:
                try:
                    nearest = engine.get_nearest(word, n=10)
                    for w, s in nearest:
                        semantic_results.append((w, s))
                except KeyError:
                    pass
            # Merge semantic results as pseudo-documents
            for word, score in semantic_results[:5]:
                hits_raw.append((
                    -len(hits_raw) - 1,  # synthetic doc_id
                    score * 0.3,
                    {"word": word, "title": word, "_semantic": True}
                ))
        except Exception:
            pass

    # ---- Stage 5: Hybrid scoring ----
    if hybrid and engine.vectors is not None:
        # Rescore: add semantic score to each hit
        query_words = tokenize(q)
        if query_words:
            query_vec = None
            for w in query_words:
                try:
                    query_vec = engine.get_word_vector(w)
                    break
                except KeyError:
                    continue

            if query_vec is not None:
                hybrid_hits = []
                for doc_id, score, data in hits_raw:
                    title = data.get("title", "")
                    title_words = tokenize(title)[:3]
                    sem_score = 0.0
                    for tw in title_words:
                        try:
                            tv = engine.get_word_vector(tw)
                            sim = float(np.dot(query_vec, tv)) / (
                                np.linalg.norm(query_vec) * np.linalg.norm(tv) + 1e-10
                            )
                            sem_score = max(sem_score, sim)
                        except KeyError:
                            continue
                    combined = score * 0.6 + sem_score * 0.4
                    hybrid_hits.append((doc_id, combined, data))
                hits_raw = hybrid_hits

    # ---- Sort by score ----
    hits_raw.sort(key=lambda x: -x[1])

    # ---- Apply offset and limit ----
    paginated = hits_raw[offset:offset + limit]

    # ---- Format response ----
    hits = []
    for doc_id, score, data in paginated:
        hit = {
            "id": doc_id,
            "score": round(score, 4),
            "positions": [{"start": 0, "length": len(q)}],
        }
        if isinstance(data, dict):
            hit.update(data)
        elif isinstance(data, str):
            hit["title"] = data
        hits.append(hit)

    return {
        "hits": hits,
        "query": q,
        "processingTimeMs": 0,  # would need start timer
        "limit": limit,
        "offset": offset,
        "estimatedTotalHits": len(hits_raw),
    }


# ======================================================================
# Word / Semantic Endpoints
# ======================================================================

@app.get("/indexes/{index_uid}/documents/{word}")
async def get_document(index_uid: str, word: str):
    """Get the embedding vector for a word."""
    engine = registry.get(index_uid)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Index not found: {index_uid}")

    if engine.ldb:
        val = engine.ldb.get(word.encode("utf-8"))
        if val:
            vec = unpack_f32s(val)
            return {"word": word, "vector": vec, "dimension": len(vec)}

    try:
        vec = engine.get_word_vector(word)
        return {"word": word, "vector": vec.tolist(), "dimension": engine.dim}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Word not found: {word}")


@app.post("/indexes/{index_uid}/analogies")
async def analogies(
    index_uid: str,
    a: str = Body(...),
    b: str = Body(...),
    c: str = Body(...),
    n: int = Body(10),
):
    """Solve word analogies: a is to b as c is to ?"""
    engine = registry.get(index_uid)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Index not found: {index_uid}")

    try:
        results = engine.get_analogies(a, b, c, n=n)
        hits = [{"word": word, "score": score} for word, score in results]
        return {"hits": hits, "analogy": f"{a} : {b} :: {c} : ?"}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/indexes/{index_uid}/vectorize")
async def vectorize(
    index_uid: str,
    text: str = Body(...),
    type: str = Body("word"),
):
    """Get the vector for a word or sentence."""
    engine = registry.get(index_uid)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Index not found: {index_uid}")

    try:
        if type == "sentence":
            vec = engine.get_sentence_vector(text)
        else:
            vec = engine.get_word_vector(text)
        return {"text": text, "type": type, "vector": vec.tolist(), "dimension": len(vec)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ======================================================================
# Main
# ======================================================================

def run_server(host: str = "0.0.0.0", port: int = 7700, data_dir: str | None = None):
    """Start the API server.

    ``data_dir`` is exported through the ``WORDSWIFT_DATA_DIR`` environment
    variable so that the worker process (which re-imports ``src.api``) resolves
    the same directory. Passing the registry alone is not enough because
    ``uvicorn.run`` with an import string builds a fresh module namespace.
    """
    global registry

    resolved = data_dir or os.environ.get("WORDSWIFT_DATA_DIR") or "data"
    resolved = str(Path(resolved).resolve())
    os.environ["WORDSWIFT_DATA_DIR"] = resolved

    # Rebind the module global that every endpoint closes over.
    registry = IndexRegistry(resolved)
    app.state.registry = registry

    # Pass the app OBJECT rather than the "src.api:app" import string.
    # uvicorn re-importing that string can resolve to a *different* module
    # namespace than the one we just patched (notably under `python -m src.api`,
    # where this file runs as __main__ while `src.api` is already in
    # sys.modules). That silently falls back to the default ./data directory.
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7700)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    run_server(args.host, args.port, args.data_dir)
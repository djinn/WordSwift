"""Shared WordSwift HTTP client for the ecommerce integrations.

All platform sync scripts import from here so index/document handling stays
consistent. Only the standard library is used — no extra dependencies.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional


class WordSwiftError(RuntimeError):
    """Raised when the WordSwift API returns an error response."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"WordSwift API error {status}: {detail}")
        self.status = status
        self.detail = detail


class WordSwiftClient:
    """Minimal client for the WordSwift MeiliSearch-compatible API.

    Args:
        base_url: Server root, e.g. ``http://localhost:7700``.
        timeout: Per-request timeout in seconds.
        batch_size: Documents per POST when bulk-adding.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:7700",
        timeout: float = 30.0,
        batch_size: int = 200,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.batch_size = batch_size

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WordSwiftError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise WordSwiftError(0, f"cannot reach {self.base_url}: {exc.reason}") from exc

    # ------------------------------------------------------------------
    # Health / index management
    # ------------------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def create_index(self, uid: str, dim: int = 100) -> Dict[str, Any]:
        """Create an index, tolerating 'already exists'."""
        try:
            return self._request("POST", "/indexes", body={"uid": uid, "dim": dim})
        except WordSwiftError as exc:
            if exc.status == 400 and "already exists" in exc.detail:
                return {"uid": uid, "status": "exists"}
            raise

    def delete_index(self, uid: str) -> bool:
        try:
            self._request("DELETE", f"/indexes/{uid}")
            return True
        except WordSwiftError as exc:
            if exc.status == 404:
                return False
            raise

    def index_exists(self, uid: str) -> bool:
        try:
            self._request("GET", f"/indexes/{uid}")
            return True
        except WordSwiftError as exc:
            return exc.status != 404

    def stats(self, uid: str) -> Dict[str, Any]:
        return self._request("GET", f"/indexes/{uid}/stats")

    def list_indexes(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/indexes").get("results", [])

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def add_documents(
        self, uid: str, documents: Iterable[Dict[str, Any]]
    ) -> int:
        """Bulk-add documents in batches. Returns the number accepted."""
        docs = list(documents)
        if not docs:
            return 0

        added = 0
        for i in range(0, len(docs), self.batch_size):
            batch = docs[i : i + self.batch_size]
            result = self._request(
                "POST", f"/indexes/{uid}/documents", body=batch
            )
            added += int(result.get("documentsCount", 0))
        return added

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search(
        self,
        uid: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        typo_tolerance: bool = True,
        prefix_search: bool = True,
        hybrid: bool = False,
        drop_semantic_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search an index and return hits.

        Args:
            drop_semantic_only: Remove the synthetic ``_semantic`` entries the
                API emits when it falls back to vocabulary-word matches. Those
                have negative ids and are not real documents.
        """
        data = self._request(
            "GET",
            f"/indexes/{uid}/search",
            params={
                "q": query,
                "limit": limit,
                "offset": offset,
                "typo_tolerance": str(typo_tolerance).lower(),
                "prefix_search": str(prefix_search).lower(),
                "hybrid": str(hybrid).lower(),
            },
        )
        hits = data.get("hits", [])
        if drop_semantic_only:
            hits = [h for h in hits if not h.get("_semantic")]
        return hits


    # ------------------------------------------------------------------
    # Embeddings / semantics
    # ------------------------------------------------------------------
    def analogies(
        self, uid: str, a: str, b: str, c: str, n: int = 10
    ) -> List[Dict[str, Any]]:
        """Solve ``a is to b as c is to ?``.

        Raises WordSwiftError(404) when the index has no trained embedding
        model or a word is outside its vocabulary.
        """
        data = self._request(
            "POST", f"/indexes/{uid}/analogies", body={"a": a, "b": b, "c": c, "n": n}
        )
        return data.get("hits", []) if data else []

    def get_document(self, uid: str, word: str) -> Dict[str, Any]:
        """Fetch the stored vector for a word/document key."""
        return self._request("GET", f"/indexes/{uid}/documents/{urllib.parse.quote(str(word), safe='')}")

    def vectorize(self, uid: str, text: str, kind: str = "word") -> List[float]:
        """Vectorize a word or sentence."""
        data = self._request(
            "POST", f"/indexes/{uid}/vectorize", body={"text": text, "type": kind}
        )
        return data.get("vector", []) if data else []

    def train(
        self,
        uid: str,
        corpus: str,
        dim: int = 100,
        epoch: int = 5,
        ws: int = 5,
        min_count: int = 5,
        lr: float = 0.05,
    ) -> Dict[str, Any]:
        """Train embeddings from a corpus path (server-side file path)."""
        return self._request(
            "POST",
            f"/indexes/{uid}/train",
            body={
                "corpus": corpus,
                "dim": dim,
                "epoch": epoch,
                "ws": ws,
                "min_count": min_count,
                "lr": lr,
            },
        )


def stringify_document(doc: Dict[str, Any], skip: Iterable[str] = ()) -> Dict[str, Any]:
    """Coerce all values to strings so WordSwift indexes them.

    ``IndexRegistry.add_documents`` only indexes ``isinstance(v, str)`` fields,
    so integers, floats, booleans and nested structures are silently dropped.
    Keeping ``id`` numeric is deliberate — the registry coerces it to int and
    falls back to an auto-increment counter for non-numeric strings.
    """
    skip = set(skip)
    out: Dict[str, Any] = {}
    for key, value in doc.items():
        if value is None:
            continue
        if key == "id":
            out[key] = value
        elif key in skip:
            out[key] = value
        elif isinstance(value, str):
            out[key] = value
        elif isinstance(value, bool):
            out[key] = "yes" if value else "no"
        elif isinstance(value, (int, float)):
            out[key] = str(value)
        elif isinstance(value, (list, tuple, set)):
            out[key] = ", ".join(str(v) for v in value)
        elif isinstance(value, dict):
            out[key] = json.dumps(value, ensure_ascii=False)
        else:
            out[key] = str(value)
    return out


def extract_numeric_id(raw: Any) -> Optional[int]:
    """Pull an integer id out of platform-specific identifiers.

    Handles Shopify GIDs (``gid://shopify/Product/123``), plain ints, and
    numeric strings. Returns None when no digits are present.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)

    text = str(raw)
    if text.isdigit():
        return int(text)

    # gid://shopify/Product/7215348211 -> 7215348211
    tail = text.rsplit("/", 1)[-1]
    if tail.isdigit():
        return int(tail)

    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def wait_for_health(
    base_url: str = "http://localhost:7700",
    timeout: float = 30.0,
    interval: float = 1.0,
) -> bool:
    """Block until WordSwift answers /health, or the timeout elapses."""
    client = WordSwiftClient(base_url, timeout=5.0)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.health().get("status") == "available":
                return True
        except WordSwiftError:
            pass
        time.sleep(interval)
    return False

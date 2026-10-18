"""WordSwift retriever for RAG pipelines.

Framework-agnostic core (`WordSwiftRetriever`) plus optional LangChain and
LlamaIndex adapters. The adapters are only imported when you use them, so the
base class has no third-party dependencies.

Basic usage:
    from wordswift_retriever import WordSwiftRetriever

    retriever = WordSwiftRetriever(index_name="knowledge_base")
    for doc in retriever.retrieve("how do I return an item?", k=5):
        print(doc.score, doc.metadata["title"])

LangChain:
    from wordswift_retriever import as_langchain_retriever
    chain_retriever = as_langchain_retriever(index_name="knowledge_base", k=5)

LlamaIndex:
    from wordswift_retriever import as_llamaindex_retriever
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

from wordswift_client import WordSwiftClient, WordSwiftError  # noqa: E402


@dataclass
class RetrievedDoc:
    """One retrieved document, framework-neutral."""

    content: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def doc_id(self) -> Any:
        return self.metadata.get("id")

    @property
    def title(self) -> str:
        return self.metadata.get("title", "")

    def as_citation(self, index: int) -> str:
        """Format as a numbered context block for prompting."""
        header = f"[{index}] {self.title}".strip()
        if self.doc_id is not None:
            header += f" (doc {self.doc_id})"
        return f"{header}\n{self.content}"


class WordSwiftRetriever:
    """Retrieves documents from a WordSwift index for RAG.

    Args:
        base_url: WordSwift server root.
        index_name: Which index to query.
        k: Default number of documents to return.
        hybrid: Combine BM25 with semantic embedding scores.
        typo_tolerance: Allow fuzzy matching on misspelled queries.
        prefix_search: Allow prefix completion.
        drop_semantic_only: Remove synthetic vocabulary hits (negative ids,
            ``_semantic: true``) that are not real documents.
        content_fields: Ordered field names to use as document text. The first
            non-empty one wins; set to a list to concatenate several.
        max_content_chars: Truncate content to keep prompts bounded.
        min_score: Drop hits below this score. **Use with care** — WordSwift
            scores are not normalised and can exceed 1.0 under hybrid mode, so
            a fixed threshold behaves differently per query. Prefer 0 (disabled)
            and rely on k plus reranking.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:7700",
        index_name: str = "default",
        k: int = 5,
        hybrid: bool = True,
        typo_tolerance: bool = True,
        prefix_search: bool = True,
        drop_semantic_only: bool = True,
        content_fields: Optional[List[str]] = None,
        max_content_chars: int = 2000,
        min_score: float = 0.0,
        timeout: float = 15.0,
    ):
        self.client = WordSwiftClient(base_url, timeout=timeout)
        self.base_url = base_url
        self.index_name = index_name
        self.k = k
        self.hybrid = hybrid
        self.typo_tolerance = typo_tolerance
        self.prefix_search = prefix_search
        self.drop_semantic_only = drop_semantic_only
        self.content_fields = content_fields or ["content", "description", "body", "text", "title"]
        self.max_content_chars = max_content_chars
        self.min_score = min_score

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    def retrieve(self, query: str, k: Optional[int] = None) -> List[RetrievedDoc]:
        """Retrieve the top-k documents for a query."""
        query = (query or "").strip()
        if not query:
            return []

        limit = k or self.k
        hits = self.client.search(
            self.index_name,
            query,
            limit=limit,
            hybrid=self.hybrid,
            typo_tolerance=self.typo_tolerance,
            prefix_search=self.prefix_search,
            drop_semantic_only=self.drop_semantic_only,
        )

        docs: List[RetrievedDoc] = []
        for hit in hits:
            score = float(hit.get("score") or 0.0)
            if self.min_score and score < self.min_score:
                continue

            content = self._extract_content(hit)
            if not content:
                continue

            metadata = {
                key: value
                for key, value in hit.items()
                if key not in ("positions",) and not key.startswith("_")
            }
            metadata["source"] = self.index_name
            docs.append(RetrievedDoc(content=content, score=score, metadata=metadata))

        return docs[:limit]

    def _extract_content(self, hit: Dict[str, Any]) -> str:
        """Pull the document body out of a hit, respecting content_fields."""
        for name in self.content_fields:
            value = hit.get(name)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                return text[: self.max_content_chars]
        return ""

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------
    def build_context(self, docs: List[RetrievedDoc]) -> str:
        """Render docs as a numbered, citable context block."""
        return "\n\n".join(doc.as_citation(i) for i, doc in enumerate(docs, 1))

    def context_for(self, query: str, k: Optional[int] = None) -> str:
        """Convenience: retrieve then render in one call."""
        return self.build_context(self.retrieve(query, k=k))


# ----------------------------------------------------------------------
# Minimal RAG loop (no LLM dependency — bring your own call_llm)
# ----------------------------------------------------------------------
SYSTEM_TEMPLATE = """Answer the question using ONLY the numbered context below.
Cite every claim inline as [n]. If the context does not contain the answer,
reply with exactly: INSUFFICIENT_CONTEXT

CONTEXT:
{context}

QUESTION: {question}

ANSWER:"""


def build_prompt(context: str, question: str) -> str:
    """Assemble the grounded-answer prompt."""
    return SYSTEM_TEMPLATE.format(context=context, question=question)


def grounded_answer(
    retriever: WordSwiftRetriever,
    question: str,
    call_llm,
    k: int = 5,
) -> Dict[str, Any]:
    """Run one grounded RAG turn.

    Args:
        retriever: Configured WordSwiftRetriever.
        question: User question.
        call_llm: Callable taking a prompt string and returning text.
        k: Documents to retrieve.

    Returns:
        Dict with ``answer``, ``sources``, ``prompt``, ``retrieved``,
        ``insufficient`` and ``bad_citations``.
    """
    import re

    docs = retriever.retrieve(question, k=k)
    context = retriever.build_context(docs)
    prompt = build_prompt(context, question)
    answer = call_llm(prompt)

    insufficient = "INSUFFICIENT_CONTEXT" in (answer or "")

    # Verify the model only cited documents we actually supplied.
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", answer or "")}
    bad_citations = sorted(n for n in cited if not 1 <= n <= len(docs))

    sources = [
        {
            "citation": i,
            "id": docs[i - 1].doc_id,
            "title": docs[i - 1].title,
            "score": docs[i - 1].score,
            "metadata": docs[i - 1].metadata,
        }
        for i in sorted(c for c in cited if 1 <= c <= len(docs))
    ]

    return {
        "answer": answer,
        "sources": sources,
        "prompt": prompt,
        "retrieved": len(docs),
        "insufficient": insufficient,
        "bad_citations": bad_citations,
    }


# ----------------------------------------------------------------------
# Optional framework adapters
# ----------------------------------------------------------------------
def as_langchain_retriever(**kwargs):
    """Return a LangChain BaseRetriever backed by WordSwift.

    Raises ImportError with install guidance if langchain is unavailable.
    """
    try:
        from langchain_core.callbacks import CallbackManagerForRetrieverRun
        from langchain_core.documents import Document
        from langchain_core.retrievers import BaseRetriever
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "LangChain is required for this adapter. Install with: "
            "pip install langchain-core"
        ) from exc

    config = dict(kwargs)

    class _WordSwiftLangChainRetriever(BaseRetriever):
        """LangChain retriever wrapping WordSwiftRetriever."""

        def _get_relevant_documents(
            self, query: str, *, run_manager: CallbackManagerForRetrieverRun
        ) -> List[Document]:
            inner = WordSwiftRetriever(**config)
            out = []
            for doc in inner.retrieve(query):
                out.append(
                    Document(page_content=doc.content, metadata=doc.metadata)
                )
            return out

    return _WordSwiftLangChainRetriever()


def as_llamaindex_retriever(**kwargs):
    """Return a LlamaIndex BaseRetriever backed by WordSwift."""
    try:
        from llama_index.core.base.base_retriever import BaseRetriever
        from llama_index.core.schema import NodeWithScore, TextNode
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "LlamaIndex is required for this adapter. Install with: "
            "pip install llama-index-core"
        ) from exc

    inner = WordSwiftRetriever(**kwargs)

    class _WordSwiftLlamaRetriever(BaseRetriever):
        def _retrieve(self, query_bundle) -> List[NodeWithScore]:
            nodes = []
            for doc in inner.retrieve(query_bundle.query_str):
                nodes.append(
                    NodeWithScore(
                        node=TextNode(
                            text=doc.content,
                            metadata=doc.metadata,
                            id_=str(doc.doc_id),
                        ),
                        score=doc.score,
                    )
                )
            return nodes

    return _WordSwiftLlamaRetriever()

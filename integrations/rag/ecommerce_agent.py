"""Agent tools backed by WordSwift retrieval.

Exposes WordSwift's three retrieval strengths as separate tools so an LLM agent
can reason about *which* strategy to use, rather than phrasing everything as
keywords:

    search_knowledge  → BM25 + fuzzy + hybrid    "what is the return policy?"
    find_similar      → hybrid semantic search   "what else is like X?"
    word_analogy      → relational reasoning     "shirt:jeans::dress:?"

Framework-neutral tool functions are provided, plus ready-made LangChain
@tool wrappers. Run this file directly for an offline demo with a stub LLM.

    export WORDSWIFT_URL=http://localhost:7700
    python3 ecommerce_agent.py --demo

Requires only the standard library for the core tools.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wordswift_client import WordSwiftClient, WordSwiftError  # noqa: E402
from wordswift_retriever import WordSwiftRetriever  # noqa: E402

WORDSWIFT_URL = os.environ.get("WORDSWIFT_URL", "http://localhost:7700")
DEFAULT_INDEX = os.environ.get("WORDSWIFT_INDEX", "knowledge_base")


# ----------------------------------------------------------------------
# Tool implementations (framework-neutral, return strings for the LLM)
# ----------------------------------------------------------------------
class EcommerceTools:
    """WordSwift-backed tools for a shopping or support agent."""

    def __init__(
        self,
        base_url: str = WORDSWIFT_URL,
        index: str = DEFAULT_INDEX,
        timeout: float = 15.0,
    ):
        self.client = WordSwiftClient(base_url, timeout=timeout)
        self.index = index
        self.retriever = WordSwiftRetriever(
            base_url, index_name=index, k=5, timeout=timeout
        )

    # -- Tool 1: document search -------------------------------------
    def search_knowledge(self, query: str, k: int = 5) -> str:
        """Search documents by keyword, meaning, or approximate spelling.

        Handles typos ("retun policty") and partial words. Returns numbered
        results with ids so the agent can cite them.
        """
        try:
            docs = self.retriever.retrieve(query, k=k)
        except WordSwiftError as exc:
            return f"Search failed: {exc}"

        if not docs:
            return f"No documents matched '{query}'."

        return "\n\n".join(doc.as_citation(i) for i, doc in enumerate(docs, 1))

    # -- Tool 2: similarity ------------------------------------------
    def find_similar(self, item: str, k: int = 5) -> str:
        """Find catalog items semantically similar to a given item name.

        Use for "what else is like X?" and cross-sell recommendations.
        """
        try:
            hits = self.client.search(
                self.index, item, limit=k, hybrid=True, typo_tolerance=False
            )
        except WordSwiftError as exc:
            return f"Similarity search failed: {exc}"

        if not hits:
            return f"No items similar to '{item}'."

        lines = []
        for hit in hits:
            title = hit.get("title") or hit.get("content") or str(hit.get("id"))
            price = hit.get("price")
            score = hit.get("score")
            line = f"- {title}"
            if price:
                line += f" (${price})"
            if score is not None:
                line += f"  [score={float(score):.3f}]"
            lines.append(line)
        return "\n".join(lines)

    # -- Tool 3: analogy ---------------------------------------------
    def word_analogy(self, a: str, b: str, c: str, n: int = 5) -> str:
        """Relational analogy: a is to b as c is to what?

        Requires a TRAINED embedding model on the index. Without one the API
        returns 404 — handled gracefully here so the agent can fall back to
        search_knowledge.
        """
        try:
            hits = self.client.analogies(self.index, a, b, c, n=n)
        except WordSwiftError as exc:
            if exc.status == 404:
                return (
                    "Analogy unavailable: this index has no trained embedding "
                    "model, or one of the words is not in its vocabulary. "
                    "Use search_knowledge instead."
                )
            return f"Analogy failed: {exc}"

        if not hits:
            return f"No analogy results for {a}:{b}::{c}."

        return "\n".join(
            f"- {h.get('word')} (score={float(h.get('score', 0)):.3f})" for h in hits
        )

    # -- Tool 4: catalog facts ---------------------------------------
    def get_product(self, product_id: Any) -> str:
        """Look up a single document's stored fields by id."""
        try:
            data = self.client.get_document(self.index, product_id)
        except WordSwiftError as exc:
            if exc.status == 404:
                return f"No document with id {product_id}."
            return f"Lookup failed: {exc}"
        return str(data)

    # -- Introspection (helps the agent and you debug) ---------------
    def index_stats(self) -> str:
        """Report how many documents are indexed."""
        try:
            stats = self.client.stats(self.index)
        except WordSwiftError as exc:
            return f"Stats unavailable: {exc}"

        # NOTE: fullTextDocuments is the real doc count. numberOfDocuments
        # reflects embedding vocabulary size and stays 0 until you train.
        return (
            f"index={self.index} "
            f"documents={stats.get('fullTextDocuments')} "
            f"vocab={stats.get('numberOfDocuments')} "
            f"dim={stats.get('dimension')}"
        )

    def tool_schemas(self) -> List[Dict[str, Any]]:
        """OpenAI-style function schemas for agent frameworks."""
        return [
            {
                "name": "search_knowledge",
                "description": (
                    "Search the catalog/knowledge base by keyword, meaning, or "
                    "approximate spelling. Handles typos. Returns numbered "
                    "results with document ids for citation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search text"},
                        "k": {"type": "integer", "description": "Max results", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "find_similar",
                "description": "Find items semantically similar to a named item. Use for recommendations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string", "description": "Item name"},
                        "k": {"type": "integer", "default": 5},
                    },
                    "required": ["item"],
                },
            },
            {
                "name": "word_analogy",
                "description": (
                    "Relational analogy a:b::c:? — e.g. shirt:jeans::dress:heels. "
                    "Needs a trained embedding model."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "b": {"type": "string"},
                        "c": {"type": "string"},
                        "n": {"type": "integer", "default": 5},
                    },
                    "required": ["a", "b", "c"],
                },
            },
            {
                "name": "index_stats",
                "description": "Report how many documents are indexed.",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

    def dispatch(self, name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool by name — the loop most agent frameworks need."""
        handlers: Dict[str, Callable[..., str]] = {
            "search_knowledge": self.search_knowledge,
            "find_similar": self.find_similar,
            "word_analogy": self.word_analogy,
            "get_product": self.get_product,
            "index_stats": lambda: self.index_stats(),
        }
        handler = handlers.get(name)
        if handler is None:
            return f"Unknown tool: {name}"
        try:
            return handler(**arguments)
        except TypeError as exc:
            return f"Bad arguments for {name}: {exc}"
        except WordSwiftError as exc:
            return f"{name} failed: {exc}"


# ----------------------------------------------------------------------
# LangChain adapter (optional)
# ----------------------------------------------------------------------
def langchain_tools(base_url: str = WORDSWIFT_URL, index: str = DEFAULT_INDEX):
    """Return LangChain @tool objects wrapping EcommerceTools.

    Raises ImportError with install guidance if langchain is missing.
    """
    try:
        from langchain_core.tools import tool
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "LangChain is required. Install with: pip install langchain-core"
        ) from exc

    tools = EcommerceTools(base_url=base_url, index=index)

    @tool
    def search_knowledge_tool(query: str, k: int = 5) -> str:
        """Search the knowledge base by keyword, meaning, or approximate spelling."""
        return tools.search_knowledge(query, k=k)

    @tool
    def find_similar_tool(item: str, k: int = 5) -> str:
        """Find items semantically similar to a given item name."""
        return tools.find_similar(item, k=k)

    @tool
    def analogy_tool(a: str, b: str, c: str, n: int = 5) -> str:
        """Solve a:b::c:? relational analogy between concepts."""
        return tools.word_analogy(a, b, c, n=n)

    @tool
    def index_stats_tool() -> str:
        """Report how many documents are indexed."""
        return tools.index_stats()

    return [
        search_knowledge_tool,
        find_similar_tool,
        analogy_tool,
        index_stats_tool,
    ]


# ----------------------------------------------------------------------
# Offline demo (no LLM required)
# ----------------------------------------------------------------------
def demo(base_url: str, index: str) -> int:
    tools = EcommerceTools(base_url=base_url, index=index)

    print(f"WordSwift: {base_url}   index: {index}\n")
    print("=== index_stats ===")
    print(tools.index_stats(), "\n")

    queries = [
        ("search_knowledge", "return policy"),
        ("search_knowledge", "retun policty"),          # typo
        ("search_knowledge", "waterprof jaket"),         # typo
        ("find_similar", "jacket"),
        ("word_analogy", None),                          # dispatched below
    ]

    for name, arg in queries:
        if arg is None:
            continue
        print(f"=== {name}({arg!r}) ===")
        print(tools.dispatch(name, {"query": arg} if name == "search_knowledge" else {"item": arg}))
        print()

    print("=== word_analogy(shirt, jeans, dress) ===")
    print(tools.dispatch("word_analogy", {"a": "shirt", "b": "jeans", "c": "dress"}))
    print()

    print("=== dispatch error handling ===")
    print("unknown tool:", tools.dispatch("nope", {}))
    print("bad args:    ", tools.dispatch("search_knowledge", {"bogus": 1}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="WordSwift agent tools")
    parser.add_argument("--demo", action="store_true", help="Run offline tool demo")
    parser.add_argument("--url", default=WORDSWIFT_URL)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument(
        "--schemas", action="store_true", help="Print OpenAI-style tool schemas"
    )
    args = parser.parse_args()

    if args.schemas:
        import json

        print(json.dumps(EcommerceTools(args.url, args.index).tool_schemas(), indent=2))
        return 0

    if args.demo:
        return demo(args.url, args.index)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

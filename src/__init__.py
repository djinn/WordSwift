"""
WordSwift — Semantic word search engine with full-text BM25, typo-tolerant fuzzy search,
prefix completion, word embeddings, and analogies.  MeiliSearch-compatible API.
LevelDB-backed persistence.  Derived from fastText.

Author: Supreet Sethi <supreet.sethi@gmail.com>
Repository: https://github.com/djinn/WordSwift
"""

__version__ = "0.2.0"
__author__ = "Supreet Sethi"
__email__ = "supreet.sethi@gmail.com"
__license__ = "MIT"
__description__ = (
    "Semantic word search engine with full-text BM25, typo-tolerant fuzzy search, "
    "prefix completion, word embeddings, analogies, and MeiliSearch-compatible API. "
    "LevelDB-backed persistence. Derived from fastText."
)
__keywords__ = [
    "word-search", "semantic-search", "fasttext", "word-embeddings",
    "word2vec", "nlp", "search-engine", "fuzzy-search", "typo-tolerant",
    "full-text-search", "bm25", "meilisearch", "leveldb", "word-analogies",
    "nearest-neighbors", "cosine-similarity", "word-vectors",
    "text-search", "information-retrieval", "natural-language-processing",
]
__classifiers__ = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Topic :: Scientific/Engineering :: Information Retrieval",
    "Topic :: Text Processing :: Linguistic",
    "Topic :: Software Development :: Libraries :: Python Modules",
]

from .api import app, IndexRegistry, run_server
from .engine import WordSwiftEngine
from .dictionary import Dictionary
from .storage import LevelDBIndex, pack_f32s, unpack_f32s
from .indexer import InvertedIndex, tokenize, normalize, levenshtein

__all__ = [
    "app", "IndexRegistry", "run_server", "WordSwiftEngine",
    "Dictionary", "LevelDBIndex", "InvertedIndex",
    "pack_f32s", "unpack_f32s",
    "tokenize", "normalize", "levenshtein",
]

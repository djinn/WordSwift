"""WordSwift — Semantic word search engine with full-text & fuzzy search.

WordSwift is a complete word embedding engine derived from fastText that also
provides full-text BM25 search, typo-tolerant fuzzy search, prefix completion,
and semantic word similarity — all through a single MeiliSearch-compatible REST
API, backed by LevelDB for persistence.

Author: Supreet Sethi <supreet.sethi@gmail.com>
Repository: https://github.com/djinn/WordSwift
"""

from setuptools import setup, find_packages

with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="wordswift",
    version="0.2.0",
    author="Supreet Sethi",
    author_email="supreet.sethi@gmail.com",
    description=(
        "Semantic word search engine: full-text BM25, typo-tolerant fuzzy search, "
        "prefix completion, word embeddings & analogies. MeiliSearch-compatible API."
    ),
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/djinn/WordSwift",
    project_urls={
        "Source Code": "https://github.com/djinn/WordSwift",
        "Bug Tracker": "https://github.com/djinn/WordSwift/issues",
        "Documentation": "https://github.com/djinn/WordSwift#readme",
        "Benchmarks": "https://github.com/djinn/WordSwift/tree/main/tests",
    },
    license="MIT",
    packages=find_packages(),
    include_package_data=True,
    scripts=["wordswift"],
    python_requires=">=3.10",
    install_requires=[
        "fastapi>=0.100.0",
        "uvicorn>=0.22.0",
        "numpy>=1.24.0",
        "python-multipart>=0.0.5",
    ],
    extras_require={
        "leveldb": ["plyvel>=1.5.0"],
        "dev": ["pytest", "requests"],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Scientific/Engineering :: Information Retrieval",
        "Topic :: Text Processing :: Linguistic",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Software Development :: Libraries :: Application Frameworks",
    ],
    keywords=[
        "word-search", "semantic-search", "fasttext", "word-embeddings",
        "word2vec", "nlp", "search-engine", "fuzzy-search", "typo-tolerant",
        "full-text-search", "bm25", "meilisearch", "leveldb", "word-analogies",
        "nearest-neighbors", "cosine-similarity", "word-vectors",
        "text-search", "information-retrieval", "natural-language-processing",
        "word-similarity", "vector-search", "embedding-search",
    ],
)
"""
WordSwift — Core word embedding engine (fastText CBOW training & inference).

Author: Supreet Sethi <supreet.sethi@gmail.com>
Repository: https://github.com/djinn/WordSwift
"""

from __future__ import annotations

import math
import random
import struct
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .dictionary import Dictionary
from .storage import LevelDBIndex, pack_f32s, unpack_f32s


TrainCallback = Callable[[float, float, float, float, int, Optional[Dict]], None]


class WordSwiftEngine:
    """FastText-derived word embedding engine with LevelDB indexing.

    Args:
        dim: Embedding vector dimension (default: 100).
        ws: Context window size  (default: 5).
        epoch: Number of training epochs (default: 5).
        min_count: Minimum word frequency threshold (default: 5).
        lr: Initial learning rate (default: 0.05).
        seed: Random seed (default: 42).
    """

    def __init__(
        self,
        dim: int = 100,
        ws: int = 5,
        epoch: int = 5,
        min_count: int = 5,
        lr: float = 0.05,
        seed: int = 42,
    ):
        self.dim = dim
        self.ws = ws
        self.epoch = epoch
        self.min_count = min_count
        self.lr = lr
        self.seed = seed

        self.dict = Dictionary()
        self.vectors: Optional[np.ndarray] = None
        self.ldb: Optional[LevelDBIndex] = None

    # ================================================================
    # Training
    # ================================================================

    def configure(self, dim=None, ws=None, epoch=None, min_count=None, lr=None):
        """Configure training parameters (overrides defaults)."""
        if dim is not None: self.dim = dim
        if ws is not None: self.ws = ws
        if epoch is not None: self.epoch = epoch
        if min_count is not None: self.min_count = min_count
        if lr is not None: self.lr = lr

    def train(self, corpus_path: str | Path, callback: TrainCallback = None) -> None:
        """Train word embeddings from a plain-text corpus file.

        Each line is treated as a document; tokens are whitespace-separated.
        """
        corpus_path = Path(corpus_path)
        if not corpus_path.exists():
            raise FileNotFoundError(f"Corpus not found: {corpus_path}")

        self._build_vocab(corpus_path)
        self._train_embeddings(corpus_path, callback)

    def _build_vocab(self, corpus_path: Path) -> None:
        """Count word frequencies and build the dictionary."""
        print(f"Building vocabulary from {corpus_path} ...", file=sys.stderr)
        with open(corpus_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for token in line.strip().split():
                    self.dict.add(token)

        self.dict.prune(self.min_count)
        print(f"  Vocabulary size: {self.dict.vocab_size}", file=sys.stderr)

    def _train_embeddings(self, corpus_path: Path, callback: TrainCallback) -> None:
        """CBOW training with stable gradient updates.

        Uses proper learning rate decay and gradient normalization.
        """
        vocab = self.dict.vocab_size
        if vocab == 0:
            raise ValueError("Empty vocabulary — nothing to train.")

        # Normalized initialization
        self.vectors = np.random.uniform(-0.5/self.dim, 0.5/self.dim,
                                        (vocab, self.dim)).astype(np.float32)
        # Normalize to unit length
        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
        self.vectors = self.vectors / np.maximum(norms, 1e-10)

        half_ws = self.ws // 2
        start_time = time.time()
        total_tokens = 0
        init_lr = self.lr

        print(f"Training CBOW (dim={self.dim}, ws={self.ws}, "
              f"epoch={self.epoch}, lr={self.lr}) ...", file=sys.stderr)

        for epoch_i in range(self.epoch):
            epoch_loss = 0.0
            tokens_in_epoch = 0
            total_loss_sum = 0.0
            total_updates = 0

            with open(corpus_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    ids = self.dict.parse_words(line)
                    if len(ids) < 2:
                        continue

                    for pos in range(len(ids)):
                        target = ids[pos]
                        context = []
                        for j in range(-half_ws, half_ws + 1):
                            if j == 0: continue
                            idx = pos + j
                            if 0 <= idx < len(ids):
                                context.append(ids[idx])
                        if not context:
                            continue

                        # Decaying learning rate
                        lr = init_lr * max(0.001, 1.0 / max(1.0, total_tokens / 10000.0 + 1.0))

                        ctx_vec = np.mean(self.vectors[context], axis=0)
                        tgt_vec = self.vectors[target]

                        # Dot product (bounded)
                        score = float(np.dot(ctx_vec, tgt_vec))
                        score = max(min(score, 15.0), -15.0)

                        # Sigmoid loss gradient
                        if score > 0:
                            sig = 1.0 / (1.0 + math.exp(-score))
                        else:
                            exp_s = math.exp(score)
                            sig = exp_s / (1.0 + exp_s)

                        # Gradient update
                        update = (1.0 - sig) * lr  # positive value
                        # Clamp update magnitude
                        if update > 0.5:
                            update = 0.5

                        # Update target
                        err = update * ctx_vec
                        err_norm = np.linalg.norm(err)
                        if err_norm > 0.5:
                            err = err * (0.5 / err_norm)

                        self.vectors[target] += err

                        # Update context
                        ctx_update = update * tgt_vec
                        ctx_norm = np.linalg.norm(ctx_update)
                        if ctx_norm > 0.5:
                            ctx_update = ctx_update * (0.5 / ctx_norm)
                        for c in context:
                            self.vectors[c] += ctx_update

                        total_updates += 1
                        total_loss_sum += abs(score)
                        total_tokens += 1

                    tokens_in_epoch += 1
                    if tokens_in_epoch % 50000 == 0 and epoch_i == 0:
                        # Re-normalize to prevent drift
                        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
                        self.vectors = self.vectors / np.maximum(norms, 1e-10)

            # Epoch renormalization
            norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
            self.vectors = self.vectors / np.maximum(norms, 1e-10)

            elapsed = time.time() - start_time
            wps = tokens_in_epoch / max(elapsed, 0.001)
            avg_loss = total_loss_sum / max(total_updates, 1)
            progress = (epoch_i + 1) / self.epoch
            remaining = (self.epoch - epoch_i - 1) / max(wps, 0.001) if wps > 0 else 0

            if callback:
                callback(wps, avg_loss, progress, remaining, total_tokens, None)

            print(f"  Epoch {epoch_i+1}/{self.epoch} — "
                  f"loss={avg_loss:.4f} "
                  f"words/sec={wps:.0f}", file=sys.stderr)

    # ================================================================
    # Save / Load
    # ================================================================

    def save(self, path: str | Path) -> None:
        """Save model to a binary file (.bin or .ws)."""
        path = Path(path)
        with open(path, "wb") as f:
            f.write(b"wordswift")
            f.write(struct.pack("<I", 1))   # version
            f.write(struct.pack("<i", self.dim))

            vocab = self.dict.vocab_size
            f.write(struct.pack("<Q", vocab))

            for w in self.dict.id2word:
                wb = w.encode("utf-8")
                f.write(struct.pack("<I", len(wb)))
                f.write(wb)
                f.write(struct.pack("<q", self.dict.words[w]))

            if self.vectors is not None:
                f.write(struct.pack("<Q", self.vectors.shape[0]))
                f.write(struct.pack("<Q", self.vectors.shape[1]))
                f.write(self.vectors.tobytes())
            else:
                f.write(struct.pack("<Q", 0))

        print(f"Model saved to {path}", file=sys.stderr)

    def load(self, path: str | Path) -> None:
        """Load model from a binary file (.bin or .ws)."""
        path = Path(path)
        with open(path, "rb") as f:
            magic = f.read(9)
            if magic != b"wordswift":
                raise ValueError(f"Not a WordSwift model: {magic}")

            f.read(4)  # version, skip
            self.dim = struct.unpack("<i", f.read(4))[0]

            vocab = struct.unpack("<Q", f.read(8))[0]
            self.dict = Dictionary()
            for _ in range(vocab):
                wlen = struct.unpack("<I", f.read(4))[0]
                wb = f.read(wlen)
                word = wb.decode("utf-8")
                count = struct.unpack("<q", f.read(8))[0]
                self.dict.words[word] = count
                self.dict.id2word.append(word)

            rows = struct.unpack("<Q", f.read(8))[0]
            if rows > 0:
                cols = struct.unpack("<Q", f.read(8))[0]
                data = f.read(rows * cols * 4)
                self.vectors = np.frombuffer(data, dtype=np.float32).reshape(rows, cols)
            else:
                self.vectors = None

        print(f"Model loaded from {path} (vocab={self.dict.vocab_size}, "
              f"dim={self.dim})", file=sys.stderr)

    # ================================================================
    # Vector Extraction
    # ================================================================

    def get_word_vector(self, word: str) -> np.ndarray:
        """Get the embedding vector for a word."""
        wid = self.dict.get_id(word)
        if wid is None:
            raise KeyError(f"Word not found in vocabulary: {word}")
        if self.vectors is None:
            raise RuntimeError("No vectors loaded — train or load a model first")
        return self.vectors[wid].copy()

    def get_sentence_vector(self, text: str) -> np.ndarray:
        """Get averaged embedding vector for a sentence."""
        ids = self.dict.parse_words(text)
        if not ids or self.vectors is None:
            return np.zeros(self.dim, dtype=np.float32)
        return np.mean(self.vectors[ids], axis=0).astype(np.float32)

    # ================================================================
    # Nearest Neighbors & Analogies
    # ================================================================

    def get_nearest(
        self,
        word: str,
        n: int = 10,
        exclude_query: bool = True,
    ) -> List[Tuple[str, float]]:
        """Find the nearest neighbors of a word by cosine similarity.

        Args:
            word: Query word.
            n: Number of results.
            exclude_query: Exclude the query word from results.

        Returns:
            List of (word, similarity) tuples sorted by similarity descending.
        """
        query = self.get_word_vector(word)
        if self.vectors is None:
            return []

        norms = np.linalg.norm(self.vectors, axis=1)
        dot = self.vectors @ query
        sims = dot / (norms * np.linalg.norm(query) + 1e-10)

        indices = np.argsort(-sims)

        results: List[Tuple[str, float]] = []
        for idx in indices:
            if exclude_query and idx == self.dict.get_id(word):
                continue
            results.append((self.dict.word_at(int(idx)), float(sims[idx])))
            if len(results) >= n:
                break

        return results

    def get_analogies(
        self,
        a: str,
        b: str,
        c: str,
        n: int = 10,
    ) -> List[Tuple[str, float]]:
        """Solve word analogies: a is to b as c is to ?

        Given words a, b, c, finds word d such that:
            vec(d) ≈ vec(b) - vec(a) + vec(c)

        Args:
            a: First word.
            b: Second word.
            c: Third word.
            n: Number of results.

        Returns:
            List of (word, similarity) tuples.
        """
        va = self.get_word_vector(a)
        vb = self.get_word_vector(b)
        vc = self.get_word_vector(c)
        vq = vb - va + vc

        if self.vectors is None:
            return []

        norms = np.linalg.norm(self.vectors, axis=1)
        dot = self.vectors @ vq
        sims = dot / (norms * np.linalg.norm(vq) + 1e-10)
        indices = np.argsort(-sims)

        results: List[Tuple[str, float]] = []
        for idx in indices:
            word = self.dict.word_at(int(idx))
            if word in (a, b, c):
                continue
            results.append((word, float(sims[idx])))
            if len(results) >= n:
                break

        return results

    # ================================================================
    # LevelDB Index Operations
    # ================================================================

    def open_index(self, db_path: str | Path) -> None:
        """Open (or create) a LevelDB index for storing word vectors."""
        self.ldb = LevelDBIndex(db_path)
        print(f"LevelDB index opened at {db_path}", file=sys.stderr)

    def close_index(self) -> None:
        """Close the LevelDB index."""
        if self.ldb:
            self.ldb.close()
            self.ldb = None

    def index_word(self, word: str) -> None:
        """Store a single word's vector into the LevelDB index."""
        if self.ldb is None:
            raise RuntimeError("No LevelDB index open. Call open_index() first.")
        vec = self.get_word_vector(word)
        key = word.encode("utf-8")
        val = pack_f32s(vec.tolist())
        self.ldb.put(key, val)

    def index_all(self, callback: Callable[[int, int], None] = None) -> None:
        """Index all vocabulary words into the LevelDB index."""
        if self.ldb is None:
            raise RuntimeError("No LevelDB index open. Call open_index() first.")
        if self.vectors is None:
            raise RuntimeError("No vectors loaded. Train or load a model first.")

        total = self.dict.vocab_size
        for i, word in enumerate(self.dict.id2word):
            vec = self.vectors[i]
            key = word.encode("utf-8")
            val = pack_f32s(vec.tolist())
            self.ldb.put(key, val)
            if callback and (i % 1000 == 0):
                callback(i, total)

        idx_size = len(self.ldb) if self.ldb else 0
        print(f"Indexed {total} words into LevelDB ({idx_size} entries)", file=sys.stderr)

    def search_index(
        self,
        query_word: str,
        n: int = 10,
        min_score: float = 0.0,
    ) -> List[Tuple[str, float]]:
        """Search the LevelDB index for nearest neighbors of a word.

        Performs a full scan of all vectors in the index (LevelDB is used
        purely as persistent key–value storage).  For very large indexes,
        consider wrapping an approximate nearest-neighbour library.

        Args:
            query_word: Query word.
            n: Maximum number of results.
            min_score: Minimum cosine similarity threshold.

        Returns:
            List of (word, score) tuples sorted by score descending.
        """
        if self.ldb is None:
            raise RuntimeError("No LevelDB index open.")
        if self.vectors is None:
            raise RuntimeError("No vectors loaded.")

        query = self.get_word_vector(query_word)
        query_norm = np.linalg.norm(query)

        if query_norm == 0:
            return []

        results: List[Tuple[str, float]] = []
        for key_bytes, val_bytes in self.ldb:
            word = key_bytes.decode("utf-8")
            if word == query_word:
                continue
            vec = np.array(unpack_f32s(val_bytes), dtype=np.float32)
            sim = float(np.dot(vec, query)) / (np.linalg.norm(vec) * query_norm + 1e-10)
            if sim >= min_score:
                results.append((word, sim))

        results.sort(key=lambda x: -x[1])
        return results[:n]

    # ================================================================
    # Stats
    # ================================================================

    @property
    def vocab_size(self) -> int:
        return self.dict.vocab_size

    @property
    def index_size(self) -> int:
        if self.ldb is None:
            return 0
        return len(self.ldb)

    def get_stats(self) -> Dict:
        """Return model statistics as a dictionary."""
        return {
            "dimension": self.dim,
            "vocab_size": self.vocab_size,
            "window": self.ws,
            "epoch": self.epoch,
            "min_count": self.min_count,
            "learning_rate": self.lr,
            "index_size": self.index_size if self.ldb else 0,
            "model_type": "cbow",
        }
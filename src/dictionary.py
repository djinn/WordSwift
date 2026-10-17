"""
WordSwift — Dictionary / Vocabulary

Author: Supreet Sethi <supreet.sethi@gmail.com>
Repository: https://github.com/djinn/WordSwift
"""

from __future__ import annotations

from typing import Dict, List, Optional


class Dictionary:
    """FastText-style vocabulary with subword hashing support."""

    def __init__(
        self,
        minn: int = 3,
        maxn: int = 6,
        bucket: int = 2_000_000,
        label_prefix: str = "__label__",
    ):
        self.minn = minn
        self.maxn = maxn
        self.bucket = bucket
        self.label_prefix = label_prefix
        self.words: Dict[str, int] = {}
        self.id2word: List[str] = []
        self._hash_seed = 1

    def add(self, word: str) -> None:
        if word in self.words:
            self.words[word] += 1
        else:
            self.words[word] = 1
            self.id2word.append(word)

    def get_id(self, word: str) -> Optional[int]:
        if word in self.words:
            return self.id2word.index(word)
        return None

    def word_at(self, idx: int) -> Optional[str]:
        if 0 <= idx < len(self.id2word):
            return self.id2word[idx]
        return None

    @property
    def vocab_size(self) -> int:
        return len(self.id2word)

    def prune(self, min_count: int) -> None:
        keep = [w for w in self.id2word if self.words.get(w, 0) >= min_count]
        self.id2word = keep
        self.words = {w: self.words[w] for w in keep}

    def _hash(self, s: str) -> int:
        """FNV-1a 32-bit hash."""
        h = self._hash_seed
        for ch in s:
            h ^= ord(ch)
            h = (h * 0x01000193) & 0xFFFFFFFF
        return h

    def get_subwords(self, word: str) -> List[int]:
        indices: List[int] = []
        indices.append(self._hash(f"<{word}") % self.bucket)
        indices.append(self._hash(word) % self.bucket)
        indices.append(self._hash(f"{word}>") % self.bucket)
        for n in range(self.minn, self.maxn + 1):
            for j in range(len(word) - n + 1):
                indices.append(self._hash(word[j:j + n]) % self.bucket)
        return indices

    def parse_words(self, line: str, max_words: int = 256) -> List[int]:
        ids: List[int] = []
        for token in line.strip().split():
            if token.startswith(self.label_prefix):
                continue
            wid = self.get_id(token)
            if wid is not None:
                ids.append(wid)
                if len(ids) >= max_words:
                    break
        return ids
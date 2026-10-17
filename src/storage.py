"""
WordSwift — LevelDB-backed index storage.

Author: Supreet Sethi <supreet.sethi@gmail.com>
Repository: https://github.com/djinn/WordSwift
"""

from __future__ import annotations

import struct
import warnings
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

_HAS_LEVELDB = False
try:
    import plyvel as _ldb
    _HAS_LEVELDB = True
except ImportError:
    try:
        import leveldb as _ldb
        _HAS_LEVELDB = True
    except ImportError:
        _ldb = None


class LevelDBIndex:
    """LevelDB-based key-value store for word embedding vectors.

    Falls back to a pure-Python dict when LevelDB is unavailable.
    """

    def __init__(self, db_path: str | Path, create: bool = True):
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self._db = None
        self._fallback: Dict[bytes, bytes] = {}

        if _HAS_LEVELDB:
            try:
                self._db = _ldb.DB(str(self.db_path), create_if_missing=create)
            except Exception as exc:
                warnings.warn(f"LevelDB open failed: {exc}; using fallback")
                self._db = None

    def put(self, key: bytes, value: bytes) -> None:
        if self._db is not None:
            self._db.put(key, value)
        else:
            self._fallback[key] = value

    def get(self, key: bytes) -> Optional[bytes]:
        if self._db is not None:
            return self._db.get(key)
        return self._fallback.get(key)

    def delete(self, key: bytes) -> None:
        if self._db is not None:
            self._db.delete(key)
        else:
            self._fallback.pop(key, None)

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        self._fallback.clear()

    def __len__(self) -> int:
        if self._db is not None:
            return sum(1 for _ in self._db)
        return len(self._fallback)

    def __iter__(self):
        if self._db is not None:
            yield from self._db
        else:
            yield from self._fallback.items()

    def keys(self) -> List[str]:
        if self._db is not None:
            return [k.decode("utf-8") for k, _ in self._db]
        return [k.decode("utf-8") for k in self._fallback]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def pack_f32s(values: List[float]) -> bytes:
    """Pack a list of floats into little-endian binary."""
    return struct.pack(f"<{len(values)}f", *values)


def unpack_f32s(data: bytes) -> List[float]:
    """Unpack little-endian binary floats."""
    count = len(data) // 4
    return list(struct.unpack(f"<{count}f", data))
"""WordSwift — fastText-powered word search engine with MeiliSearch-like API."""

from .engine import WordSwiftEngine
from .dictionary import Dictionary
from .storage import LevelDBIndex, pack_f32s, unpack_f32s
from .api import app, IndexRegistry, run_server

__version__ = "0.1.0"
__author__ = "Supreet Sethi <supreet.sethi@gmail.com>"
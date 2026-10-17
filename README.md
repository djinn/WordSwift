# WordSwift 🔍

> **fastText-powered semantic word search engine with a MeiliSearch-compatible HTTP API**

WordSwift is a complete word embedding engine derived from [fastText](https://github.com/facebookresearch/fastText).  
It provides a **MeiliSearch-style REST API** for semantic word search, nearest-neighbor lookup, analogies, and text vectorization — backed by **LevelDB** indexes for persistence and speed.

## Author

**Supreet Sethi**  
supreet.sethi@gmail.com · [LinkedIn](https://www.linkedin.com/in/djinn)  
Repository: [https://github.com/djinn/WordSwift](https://github.com/djinn/WordSwift)

---

## Features

- **Train** CBOW/skip-gram word embeddings from text corpora (full fastText implementation)
- **Search** semantically similar words using cosine similarity
- **Query** word vectors, sentence vectors, analogies
- **Index** embeddings in LevelDB for instant lookups and persistence
- **API** — MeiliSearch-compatible endpoints: `GET /indexes/{name}/search`, `POST /indexes`, `GET /indexes/{name}/documents`, etc.
- **Multi-index** support — manage multiple independent embedding indexes
- **CLI** — train, search, query, index management from the command line

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Train embeddings from a text corpus
wordswift train --corpus data/corpus.txt --output vectors.bin

# Start the API server
wordswift serve --index vectors.bin

# Search via API
curl "http://localhost:7700/indexes/vectors/search?q=king&limit=10"

# Or via CLI
wordswift nearest vectors.bin king -n 10
```

## API Endpoints (MeiliSearch-compatible)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/indexes` | Create/open an embedding index |
| `GET` | `/indexes` | List all indexes |
| `GET` | `/indexes/{name}` | Get index info (stats) |
| `DELETE` | `/indexes/{name}` | Delete an index |
| `POST` | `/indexes/{name}/train` | Train word embeddings |
| `GET` | `/indexes/{name}/search` | Semantic word search |
| `POST` | `/indexes/{name}/documents` | Add custom word vectors |
| `GET` | `/indexes/{name}/documents/{word}` | Get a word's vector |
| `POST` | `/indexes/{name}/analogies` | Solve word analogies |
| `POST` | `/indexes/{name}/vectorize` | Get sentence/word vector |
| `GET` | `/indexes/{name}/stats` | Index statistics |

## License

MIT License — see [LICENSE](LICENSE)
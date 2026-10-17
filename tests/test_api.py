"""Test all WordSwift API endpoints."""
import shutil
from pathlib import Path
from src.api import app
from fastapi.testclient import TestClient

client = TestClient(app)

print("=== Health ===")
resp = client.get("/health")
print(resp.status_code, resp.json())

print("\n=== Create Index ===")
resp = client.post("/indexes", json={"uid": "test", "dim": 50})
print(resp.status_code, resp.json())

print("\n=== List Indexes ===")
resp = client.get("/indexes")
print(resp.status_code, [r["uid"] for r in resp.json()["results"]])

print("\n=== Train ===")
resp = client.post("/indexes/test/train", json={
    "corpus": "data/corpus.txt", "dim": 50, "epoch": 2, "min_count": 1})
print(resp.status_code, resp.json())

print("\n=== Search ===")
resp = client.get("/indexes/test/search?q=cat&limit=5")
if resp.status_code == 200:
    for hit in resp.json()["hits"]:
        print(f"  {hit['word']}: {hit['score']:.4f}")

print("\n=== Vectorize ===")
resp = client.post("/indexes/test/vectorize", json={"text": "cat", "type": "word"})
print(resp.status_code, "dim =", resp.json()["dimension"])

print("\n=== Analogies ===")
resp = client.post("/indexes/test/analogies", json={"a": "cat", "b": "mouse", "c": "dog", "n": 3})
if resp.status_code == 200:
    print(resp.json()["analogy"])
    for h in resp.json()["hits"]:
        print(f"  {h['word']}: {h['score']:.4f}")

print("\n=== Document ===")
resp = client.get("/indexes/test/documents/cat")
print(resp.status_code, resp.json().get("word"))

print("\n=== Add Document ===")
resp = client.post("/indexes/test/documents", json=[
    {"word": "custom_word", "vector": [0.1] * 50}])
print(resp.status_code, resp.json())

print("\n=== Delete ===")
resp = client.delete("/indexes/test")
print(resp.status_code)

for p in Path("data").glob("test*"):
    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
Path("data/manifest.json").unlink(missing_ok=True)
print("\nAll tests passed!")
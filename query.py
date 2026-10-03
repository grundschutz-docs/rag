#!/usr/bin/env python3
"""Embed a question and search the Qdrant collection built by index.py."""
import argparse
import os
import sys

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from index import CONFIG_FILENAME, embed_batch  # noqa: E402 -- reuses index.py's provider logic, no duplication

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME))


def run(args: argparse.Namespace):
    client = QdrantClient(url=args.qdrant_url, api_key=args.qdrant_api_key or None)
    vector = embed_batch(args, [args.question])[0]
    hits = client.query_points(collection_name=args.collection, query=vector, limit=args.limit).points

    if not hits:
        print("No results. Did you run index.py first?")
        return

    for hit in hits:
        payload = hit.payload or {}
        print(f"[{hit.score:.3f}] {payload.get('id')} – {payload.get('title', '')} ({payload.get('modal_verb', '')})")
        print(f"  {payload.get('source_url', '')}")
        statement = payload.get("statement") or ""
        print(f"  {statement[:200]}{'...' if len(statement) > 200 else ''}")
        print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("question")
    p.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "grundschutzpp"))
    p.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    p.add_argument("--qdrant-api-key", default=os.environ.get("QDRANT_API_KEY", ""))
    p.add_argument("--provider", choices=["ollama", "openai"], default=os.environ.get("EMBEDDING_PROVIDER", "ollama"))
    p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    p.add_argument("--openai-api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    p.add_argument("--embedding-model", default=os.environ.get("EMBEDDING_MODEL"))
    p.add_argument("--limit", type=int, default=5)
    args = p.parse_args()
    if not args.embedding_model:
        args.embedding_model = "bge-m3" if args.provider == "ollama" else "text-embedding-3-small"
    return args


if __name__ == "__main__":
    try:
        run(parse_args())
    except Exception as e:
        print(f"query failed: {e}", file=sys.stderr)
        sys.exit(1)

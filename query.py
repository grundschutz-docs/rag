#!/usr/bin/env python3
"""Embed a question and search the Qdrant collection built by index.py."""
import argparse
import os
import sys

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from index import CONFIG_FILENAME, add_embedding_args, check_collection_dim, embed_batch, warn_about_old_config  # noqa: E402 -- reuses index.py's embedding logic, no duplication

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME))


def run(args: argparse.Namespace):
    client = QdrantClient(url=args.qdrant_url, api_key=args.qdrant_api_key or None)
    vector = embed_batch(args, [args.question])[0]
    if client.collection_exists(args.collection):
        check_collection_dim(client, args.collection, len(vector), args.embedding_model)
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
    add_embedding_args(p)
    p.add_argument("--limit", type=int, default=5)
    warn_about_old_config()
    return p.parse_args()


if __name__ == "__main__":
    try:
        run(parse_args())
    except Exception as e:
        print(f"query failed: {e}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""data/grundschutzpp-controls.jsonl -> embeddings (Ollama or OpenAI) -> Qdrant upsert.

Assumes Qdrant and (if using the local provider) Ollama are already running
somewhere reachable -- this script only indexes, it doesn't stand up
infrastructure. If you don't have those yet, docker-compose.yml gives you
both locally.
"""
import argparse
import json
import logging
import os
import sys
import time
import uuid

import requests
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from tqdm import tqdm

CONFIG_FILENAME = "index.conf"  # not ".env": matches this project's other ingest tools
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("index")

# Fixed once, used for every point ID this script ever creates -- not meant
# to match any other project's namespace, just needs to stay constant here.
NAMESPACE = uuid.UUID("9b6c3a1e-2f8d-4b7a-9e3c-1a5f7d2b6e40")
DEFAULT_JSONL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "grundschutzpp-controls.jsonl")


def load_records(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def embed_batch_ollama(ollama_url: str, model: str, texts: list[str], retries: int = 5) -> list[list[float]]:
    url = f"{ollama_url.rstrip('/')}/api/embed"
    delay = 2.0
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json={"model": model, "input": texts}, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings")
            if not embeddings or len(embeddings) != len(texts):
                raise RuntimeError(f"unexpected embedding response shape: {data.keys()}")
            return embeddings
        except (requests.RequestException, RuntimeError) as e:
            if attempt == retries:
                raise
            log.warning("embed_batch (ollama) attempt %d/%d failed: %s (retrying in %.1fs)", attempt, retries, e, delay)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def embed_batch_openai(api_key: str, model: str, texts: list[str], retries: int = 5) -> list[list[float]]:
    url = "https://api.openai.com/v1/embeddings"
    headers = {"Authorization": f"Bearer {api_key}"}
    delay = 2.0
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, headers=headers, json={"model": model, "input": texts}, timeout=120)
            resp.raise_for_status()
            data = resp.json()["data"]
            return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]
        except (requests.RequestException, KeyError) as e:
            if attempt == retries:
                raise
            log.warning("embed_batch (openai) attempt %d/%d failed: %s (retrying in %.1fs)", attempt, retries, e, delay)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def embed_batch(args: argparse.Namespace, texts: list[str]) -> list[list[float]]:
    if args.provider == "ollama":
        return embed_batch_ollama(args.ollama_url, args.embedding_model, texts)
    if args.provider == "openai":
        if not args.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set (index.conf or --openai-api-key)")
        return embed_batch_openai(args.openai_api_key, args.embedding_model, texts)
    raise ValueError(f"unknown provider: {args.provider!r}")


def point_id_for(control_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, control_id))


def ensure_collection(client: QdrantClient, name: str, dim: int):
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
    )
    log.info("created collection %s (dim=%d, cosine)", name, dim)


def run(args: argparse.Namespace):
    records = load_records(args.file)
    log.info("loaded %d controls from %s", len(records), args.file)

    client = QdrantClient(url=args.qdrant_url, api_key=args.qdrant_api_key or None)

    dim_probe = embed_batch(args, [records[0]["text"]])
    ensure_collection(client, args.collection, dim=len(dim_probe[0]))

    for i in tqdm(range(0, len(records), args.batch_size), desc=f"embedding ({args.provider}) + upserting"):
        batch = records[i : i + args.batch_size]
        vectors = embed_batch(args, [r["text"] for r in batch])
        points = [
            qm.PointStruct(id=point_id_for(r["id"]), vector=vec, payload=r)
            for r, vec in zip(batch, vectors)
        ]
        # Point ID is deterministic from the control ID -- re-running this
        # script after a catalog update overwrites existing points in place
        # instead of duplicating them.
        client.upsert(collection_name=args.collection, points=points, wait=True)

    log.info("done: %d controls embedded via '%s' and upserted into '%s'", len(records), args.provider, args.collection)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", default=os.environ.get("JSONL_FILE", DEFAULT_JSONL))
    p.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "grundschutzpp"))
    p.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    p.add_argument("--qdrant-api-key", default=os.environ.get("QDRANT_API_KEY", ""))
    p.add_argument("--provider", choices=["ollama", "openai"], default=os.environ.get("EMBEDDING_PROVIDER", "ollama"))
    p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    p.add_argument("--openai-api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    p.add_argument(
        "--embedding-model",
        default=os.environ.get("EMBEDDING_MODEL"),
        help="default: bge-m3 for ollama, text-embedding-3-small for openai",
    )
    p.add_argument("--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", 32)))
    args = p.parse_args()
    if not args.embedding_model:
        args.embedding_model = "bge-m3" if args.provider == "ollama" else "text-embedding-3-small"
    return args


if __name__ == "__main__":
    try:
        run(parse_args())
    except Exception:
        log.exception("index failed")
        sys.exit(1)

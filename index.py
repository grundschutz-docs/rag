#!/usr/bin/env python3
"""data/grundschutzpp-controls.jsonl -> embeddings (any OpenAI-compatible API) -> Qdrant upsert.

Assumes Qdrant and an embedding server (Ollama, vLLM, OpenAI, ...) are
already running somewhere reachable -- this script only indexes, it doesn't
stand up infrastructure. If you don't have those yet, docker-compose.yml gives
you Qdrant and Ollama locally.
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

CONFIG_FILENAME = ".env"
LEGACY_CONFIG_FILENAME = "index.conf"
HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(HERE, CONFIG_FILENAME))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("index")

# Ollama's own OpenAI-compatible endpoint, so a bare checkout works with the
# docker-compose.yml setup without any configuration.
DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "bge-m3"
# Settings of the earlier provider switch. They would otherwise be ignored
# without a word -- and a leftover OPENAI_API_KEY would then quietly send
# the run to the local Ollama instead of OpenAI.
REMOVED_SETTINGS = ("EMBEDDING_PROVIDER", "OLLAMA_URL", "OPENAI_API_KEY")

# Fixed once, used for every point ID this script ever creates -- not meant
# to match any other project's namespace, just needs to stay constant here.
NAMESPACE = uuid.UUID("9b6c3a1e-2f8d-4b7a-9e3c-1a5f7d2b6e40")
DEFAULT_JSONL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "grundschutzpp-controls.jsonl")


def load_records(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def warn_about_old_config():
    if os.path.exists(os.path.join(HERE, LEGACY_CONFIG_FILENAME)):
        log.warning("%s is no longer read -- copy your settings to %s (see .env.example)", LEGACY_CONFIG_FILENAME, CONFIG_FILENAME)
    stale = [k for k in REMOVED_SETTINGS if os.environ.get(k)]
    if stale:
        log.warning(
            "ignoring removed setting(s): %s -- set EMBEDDING_BASE_URL, EMBEDDING_API_KEY and EMBEDDING_MODEL instead (see .env.example)",
            ", ".join(stale),
        )


def embed_batch(args: argparse.Namespace, texts: list[str], retries: int = 5) -> list[list[float]]:
    """POST to {base_url}/embeddings, the one endpoint every OpenAI-compatible server shares."""
    url = f"{args.base_url.rstrip('/')}/embeddings"
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    delay = 2.0
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, headers=headers, json={"model": args.embedding_model, "input": texts}, timeout=120)
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                # Wrong URL, key or model name: retrying for half a minute won't fix it.
                raise RuntimeError(f"{url} answered {resp.status_code}: {resp.text[:300]}")
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))  # stable: keeps server order if a server omits "index"
            if len(data) != len(texts):
                raise ValueError(f"sent {len(texts)} texts, got {len(data)} embeddings back")
            return [item["embedding"] for item in data]
        except (requests.RequestException, KeyError, ValueError) as e:
            if attempt == retries:
                raise
            log.warning("embed_batch attempt %d/%d failed: %s (retrying in %.1fs)", attempt, retries, e, delay)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def add_embedding_args(p: argparse.ArgumentParser):
    """The settings index.py and query.py share, so both read them the same way."""
    p.add_argument("--base-url", default=os.environ.get("EMBEDDING_BASE_URL") or DEFAULT_BASE_URL,
                   help="OpenAI-compatible API root, e.g. http://localhost:11434/v1")
    p.add_argument("--api-key", default=os.environ.get("EMBEDDING_API_KEY", ""), help="leave empty for local servers")
    p.add_argument("--embedding-model", default=os.environ.get("EMBEDDING_MODEL") or DEFAULT_MODEL)


def point_id_for(control_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, control_id))


def check_collection_dim(client: QdrantClient, name: str, dim: int, model: str):
    """A collection fixes its vector size when created. Another model usually returns another size."""
    vectors = client.get_collection(name).config.params.vectors
    existing = vectors.size if isinstance(vectors, qm.VectorParams) else None  # named vectors: not made by this script
    if existing is not None and existing != dim:
        raise RuntimeError(
            f"collection '{name}' holds {existing}-dimensional vectors, but model '{model}' returns {dim}. "
            f"Set QDRANT_COLLECTION to a new name for this model, or delete the old collection."
        )


def ensure_collection(client: QdrantClient, name: str, dim: int, model: str):
    if client.collection_exists(name):
        check_collection_dim(client, name, dim, model)
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
    ensure_collection(client, args.collection, dim=len(dim_probe[0]), model=args.embedding_model)

    for i in tqdm(range(0, len(records), args.batch_size), desc=f"embedding ({args.embedding_model}) + upserting"):
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

    log.info("done: %d controls embedded with '%s' and upserted into '%s'", len(records), args.embedding_model, args.collection)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", default=os.environ.get("JSONL_FILE", DEFAULT_JSONL))
    p.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "grundschutzpp"))
    p.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    p.add_argument("--qdrant-api-key", default=os.environ.get("QDRANT_API_KEY", ""))
    add_embedding_args(p)
    p.add_argument("--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", 32)))
    warn_about_old_config()
    return p.parse_args()


if __name__ == "__main__":
    try:
        run(parse_args())
    except Exception:
        log.exception("index failed")
        sys.exit(1)

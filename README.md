# grundschutz-docs/rag

The BSI's Grundschutz++ catalog, exported as plain text + metadata (JSONL, no embeddings baked in) and a small script to embed and index it in [Qdrant](https://qdrant.tech) — locally for free, or with a paid API, your choice either way.

This is the data pipeline behind [grundschutz-docs.de](https://grundschutz-docs.de), the readable rendering of the same catalog, for people who want to build their own RAG/chatbot/search tool on top of it instead of reading the site.

## Quickstart

Already have Qdrant and an embedding server (Ollama, vLLM, OpenAI, …) running somewhere? Then this is the whole thing:

```bash
git clone https://github.com/grundschutz-docs/rag.git
cd rag
cp .env.example .env   # point it at your Qdrant and embedding server, or leave the localhost defaults
./run.sh               # embeds data/grundschutzpp-controls.jsonl, upserts into Qdrant
./query.sh "Wie oft muss ein Penetrationstest durchgeführt werden?"
```

`run.sh`/`query.sh` bootstrap a local `.venv` and install `requirements.txt` on first run — no manual install step, no project-wide package to publish. `data/grundschutzpp-controls.jsonl` is already in this repo and kept in sync daily, so there's nothing to generate before you start.

### Don't have Qdrant/Ollama yet?

```bash
docker compose up -d   # local Qdrant + local Ollama, bge-m3 pulled automatically
```

then run the quickstart above against the defaults in `.env.example` (`localhost:6333` / `localhost:11434`) — nothing else changes.

## Architecture

```
BSI-Bund/Stand-der-      export.py         data/grundschutzpp-
Technik-Bibliothek  ───▶  (daily CI)   ───▶  controls.jsonl
                          OSCAL → JSONL      (committed to this repo)
                                                      │
                                                      ▼
                                              run.sh → index.py
                                                      │
                                     ┌────────────────┴────────────────┐
                                     ▼                                 ▼
                          embed via Ollama / vLLM          embed via OpenAI /
                          (your own server)                any other API
                                     │                                 │
                                     └────────────────┬────────────────┘
                                         one setting: EMBEDDING_BASE_URL
                                                      ▼
                                         Qdrant (local or Cloud --
                                         same client, just a different URL)
                                                      │
                                                      ▼
                                    query.sh "<your question>"
```

`export.py` never touches an embedding model or Qdrant — it only turns the OSCAL catalog into clean JSONL. Which embedding server and which Qdrant you point at is a config choice (`.env`, or `--base-url`/`--qdrant-url` flags), not a code fork. The only requirement on the embedding server is that it speaks the OpenAI embeddings API (`POST {base_url}/embeddings`); `index.py` and `query.py` share the same `embed_batch()`.

## The data: `data/grundschutzpp-controls.jsonl`

One JSON object per line, one line per control/requirement (1000 total — every node in the catalog carries its own statement, including ones with sub-requirements, not just leaves). Fields:

| Field | Description |
|---|---|
| `id` | Control ID, e.g. `"GC.1.1"` |
| `title` | Control title |
| `group_id`, `group_title` | The practice it belongs to, e.g. `"GC"`, `"Governance und Compliance"` |
| `modal_verb` | `"MUSS"` / `"SOLLTE"` / `"KANN"` |
| `sec_level` | `"normal-SdT"` or `"erhöht"` |
| `effort_level` | `"0"`–`"5"` |
| `tags` | `[{name, hint, synonyms}]` |
| `threats` | `[{id, label}]` — base threats (Gefährdungen) this control addresses |
| `predecessors` | `[{old_id, relationship, pdf_url}]` — link to the old Kompendium requirement, **not** its full text (see Licensing) |
| `statement`, `guidance` | Raw requirement prose and explanatory text |
| `text` | Statement + guidance pre-composed into one block, ready to embed as-is |
| `source_url` | Canonical link back to the live, readable page |
| `license`, `catalog_source`, `generated_at` | Provenance |

`index.py` embeds the `text` field and stores the whole record as the Qdrant point's payload — use `text` as-is, or build your own composition from the raw fields (e.g. fold `tags` into the embedded text for better keyword alignment).

## Configuration (`.env`, see `.env.example`)

| Variable | Default | Notes |
|---|---|---|
| `QDRANT_URL` | `http://localhost:6333` | point this at Qdrant Cloud instead and nothing else changes |
| `QDRANT_API_KEY` | — | only needed for Qdrant Cloud |
| `QDRANT_COLLECTION` | `grundschutzpp` | |
| `EMBEDDING_BASE_URL` | `http://localhost:11434/v1` | root of any OpenAI-compatible API: the Ollama from `docker-compose.yml`, vLLM, OpenAI, … |
| `EMBEDDING_MODEL` | `bge-m3` | the model name as that server knows it |
| `EMBEDDING_API_KEY` | — | only for servers that require a key; leave empty for local ones |
| `BATCH_SIZE` | `32` | |

Everything is also available as a CLI flag (`./run.sh --base-url https://api.openai.com/v1 --embedding-model text-embedding-3-small --api-key sk-… --collection my-collection`), which overrides `.env`.

**Switching models:** every model returns vectors of its own size (bge-m3: 1024, for instance). A Qdrant collection fixes that size when it is created, so after changing `EMBEDDING_MODEL` use a new `QDRANT_COLLECTION` or delete the old one. Indexing and querying stop with a message that says so if the sizes do not match.

**Coming from an earlier version?** `index.conf` is now `.env`, and `EMBEDDING_PROVIDER`, `OLLAMA_URL` and `OPENAI_API_KEY` were replaced by the three `EMBEDDING_*` settings above. The scripts warn if they still find the old names.

Re-running `./run.sh` after a catalog update is safe — each control's Qdrant point ID is deterministic (derived from its control ID), so re-imports overwrite in place instead of duplicating.

## Regenerating the export yourself

You only need this if you don't trust the committed `data/grundschutzpp-controls.jsonl` or want to point it at a different catalog edition. `export.py` is pure stdlib (no install step) and reads the OSCAL catalog plus three BSI-provided auxiliary files (`basethreats.csv`, `tags.csv`, the old↔new mapping JSON) from a local sibling checkout of [`BSI-Bund/Stand-der-Technik-Bibliothek`](https://github.com/BSI-Bund/Stand-der-Technik-Bibliothek) by default, same layout as [`grundschutz-docs/docs`](https://github.com/grundschutz-docs/docs) — or via the `GRUNDSCHUTZPP_*` environment variables (see `.env.example`), same convention `.github/workflows/export.yml` uses in CI.

```bash
python3 export.py
```

## Licensing

- **Code** in this repo: MIT (see `LICENSE`).
- **Catalog content** in `data/grundschutzpp-controls.jsonl` (`statement`, `guidance`, titles, tag hints, threat labels): **CC BY-SA 4.0, © BSI-Bund**, per the upstream catalog's own license — same as [`grundschutz-docs/docs`](https://github.com/grundschutz-docs/docs#license). If you build something on top of this data, attribute the BSI and keep the same license on derivatives of the catalog text itself.
- **Old IT-Grundschutz-Kompendium**: not reproduced here in any form. `predecessors` only carries the old requirement's ID, its relationship to the new one, and a link to BSI's own PDF — the old Kompendium's license terms are unclear for redistribution, so this repo (like `grundschutz-docs/docs`) only links to it, never copies its text.

This project is unofficial and independent — not affiliated with, endorsed by, or produced by the Bundesamt für Sicherheit in der Informationstechnik (BSI).

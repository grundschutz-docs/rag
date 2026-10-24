# grundschutz-docs/rag

The BSI's Grundschutz++ catalog as data a language model can search: one JSONL file with all 1000 requirements, and a script that embeds it into [Qdrant](https://qdrant.tech). Runs entirely on your own hardware, or against any OpenAI-compatible API.

This is the data pipeline behind [grundschutz-docs.de](https://grundschutz-docs.de), the readable rendering of the same catalog. It is for people who want to build their own RAG, chatbot or search on top of the catalog instead of reading the site.

## TL;DR

- **RAG** means: search first, then answer. An assistant fetches the matching requirements from the catalog and answers from those passages, not from the language model's memory.
- **What it enables:** ask questions in your own words and get hits with ID and a link to the source; search by meaning instead of keyword; filter first (for example `MUSS` only, or one security level); hold your own policies up against the catalog.
- **What is here:** `data/grundschutzpp-controls.jsonl`, kept in sync with the BSI catalog every day, and `run.sh` / `query.sh` to index and search it.

## Why Grundschutz++ suits this

The BSI publishes Grundschutz++ in [OSCAL](https://pages.nist.gov/OSCAL/), a machine-readable format for security requirements. The old IT-Grundschutz-Kompendium exists as PDF, Word and DocBook XML, that is, as a document.

| | Kompendium 2023 | Grundschutz++ |
|---|---|---|
| Format | PDF, Word, DocBook XML, one document per Baustein | OSCAL JSON, one catalog |
| Unit | Document structure of chapters and running text | Every requirement is its own entry with its own text |
| Properties | Level and roles as abbreviations in the text | `MUSS`/`SOLLTE`/`KANN`, security level, effort 0–5, threats and tags are fields |
| Identifier | Baustein ID, references in running text | Stable ID that stays when the wording changes |
| Version | One edition, last in 2023 | Versioned, every change can be traced |
| License | Free downloads for non-commercial purposes only; any further use needs the BSI's prior consent | CC BY-SA 4.0: redistribution and commercial use allowed, with attribution |

The license row decides what you can build: with Grundschutz++ you can embed the text and pass the result on, including in a product. For the Kompendium, the [BSI's terms of use](https://www.bsi.bund.de/DE/Service/Nutzungsbedingungen/Nutzungsbedingungen_node.html) do not allow that without consent, which is why this repository never contains its text.

## Quickstart

You need Python 3 and Git. The scripts create a virtual environment and install `requirements.txt` on first run.

**Qdrant and an embedding server (Ollama by default) are already running:**

```bash
git clone https://github.com/grundschutz-docs/rag.git
cd rag
cp .env.example .env   # point it at your Qdrant and embedding server, or leave the localhost defaults
./run.sh               # embeds data/grundschutzpp-controls.jsonl, upserts into Qdrant
./query.sh "Wie oft muss ein Penetrationstest durchgeführt werden?"
```

**Nothing installed yet:** `docker compose up -d` starts a local Qdrant and a local Ollama and pulls the bge-m3 model. Then run the commands above, the defaults in `.env.example` (`localhost:6333` for Qdrant, `localhost:11434` for Ollama) fit.

**A different embedding server:** set `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` and, if the server wants one, `EMBEDDING_API_KEY` in `.env`. The only requirement is that the server speaks the OpenAI embeddings API. See [Configuration](#configuration).

`query.sh` prints the most similar requirements with ID, score and link. Turning them into an answer takes a language model, which you connect yourself. That step is not part of this repository.

## How it works

```
BSI-Bund/Stand-der-      export.py         data/grundschutzpp-
Technik-Bibliothek  ───▶  (daily CI)   ───▶  controls.jsonl
                          OSCAL → JSONL      (committed to this repo)
                                                      │
                                                      ▼
                                              run.sh → index.py
                                                      │
                                 POST {EMBEDDING_BASE_URL}/embeddings
                          ┌───────────────┬───────────┴───────┐
                          ▼               ▼                   ▼
                       Ollama           vLLM        OpenAI / any other API
                          └───────────────┴───────────┬───────┘
                                                      ▼
                                         Qdrant (local or Cloud --
                                         same client, just a different URL)
                                                      │
                                                      ▼
                                    query.sh "<your question>"
```

1. **The BSI publishes the catalog** as OSCAL JSON in the public Stand-der-Technik-Bibliothek repository. That is the only source.
2. **`export.py` turns it into lines.** It runs daily in CI and needs only Python's standard library. It writes one line per requirement, resolves threats and tags into plain text, attaches the links to the old requirements, and joins title, verb, requirement and guidance into one `text` block that can be embedded as it is. It never touches an embedding model or Qdrant.
3. **The JSONL file is committed here**, with no embeddings baked in. You choose the model, and the file stays useful for any other tool.
4. **`run.sh` embeds and indexes.** Each `text` block becomes a vector, a row of numbers that represents its meaning. Vector and full record are stored in Qdrant. The point ID derives from the control ID, so re-running after a catalog update overwrites entries in place instead of duplicating them.
5. **`query.sh` searches.** The question is embedded with the same model, and Qdrant returns the nearest requirements.

## Running it yourself

A question such as "Which requirements concern our backups?" says something about your own IT. The catalog is public, your questions and your policies are not. Where they go is a setting:

| Setup | What leaves your environment |
|---|---|
| Qdrant and the embedding server both local (the `docker-compose.yml` default) | Nothing. Only the one-time download of the scripts and the model. |
| An embedding API such as OpenAI | The catalog text when indexing, and every search question. |
| Qdrant Cloud | The index with the catalog text, and search queries as vectors. |

The embedding server is only an address in `.env`, so moving between these is a configuration change, not a code change. The answering step (a language model) is outside this repository; there, too, you can choose a model you run yourself.

## The data: `data/grundschutzpp-controls.jsonl`

One JSON object per line, one line per control/requirement (1000 total — every node in the catalog carries its own statement, including ones with sub-requirements, not just leaves).

```json
{
  "id": "NOT.4.10",
  "title": "Getrennte Aufbewahrung",
  "modal_verb": "SOLLTE",
  "sec_level": "normal-SdT",
  "effort_level": "3",
  "statement": "Notfallplanung SOLLTE die Datensicherung getrennt von den Originaldaten platzieren.",
  "text": "NOT.4.10 – Getrennte Aufbewahrung …",
  "source_url": "https://grundschutz-docs.de/grundschutzpp/not/#NOT.4.10",
  "…": "(shortened)"
}
```

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
| `predecessors` | `[{old_id, relationship, pdf_url}]` — link to the old Kompendium requirement, **not** its text (see [Licensing](#licensing)) |
| `statement`, `guidance` | Raw requirement prose and explanatory text |
| `text` | Statement + guidance pre-composed into one block, ready to embed as-is |
| `source_url` | Canonical link back to the live, readable page |
| `license`, `catalog_source`, `generated_at` | Provenance |

`index.py` embeds the `text` field and stores the whole record as the Qdrant point's payload. Use `text` as-is, or build your own composition from the raw fields (for example fold `tags` into the embedded text for better keyword alignment). Because the payload holds all fields, Qdrant filters can use them (`modal_verb`, `sec_level`, …).

`predecessors` comes from the BSI's mapping file between the old and the new catalog. That file is marked `status: draft` by the BSI, and 17 Bausteine have no mapping yet. [The finding](https://grundschutz-docs.de/befunde/mapping-datei-ist-entwurf/) (German) has the details.

## Configuration

Settings live in `.env` (copy `.env.example`). Every one is also a CLI flag, for example `./run.sh --base-url http://localhost:8000/v1 --embedding-model BAAI/bge-m3 --collection my-collection`, and flags override `.env`.

| Variable | Default | Notes |
|---|---|---|
| `QDRANT_URL` | `http://localhost:6333` | point this at Qdrant Cloud instead and nothing else changes |
| `QDRANT_API_KEY` | — | only needed for Qdrant Cloud |
| `QDRANT_COLLECTION` | `grundschutzpp` | |
| `EMBEDDING_BASE_URL` | `http://localhost:11434/v1` | root of any OpenAI-compatible API: the Ollama from `docker-compose.yml`, vLLM, OpenAI, … |
| `EMBEDDING_MODEL` | `bge-m3` | the model name as that server knows it |
| `EMBEDDING_API_KEY` | — | only for servers that require a key; leave empty for local ones |
| `BATCH_SIZE` | `32` | texts per request |

Keep `EMBEDDING_API_KEY` in `.env` rather than passing `--api-key`, so the key does not end up in your shell history. `.env` is git-ignored.

**Switching models:** every model returns vectors of its own size (bge-m3: 1024, for instance). A Qdrant collection fixes that size when it is created, so after changing `EMBEDDING_MODEL` use a new `QDRANT_COLLECTION` or delete the old one. Indexing and querying stop with a message that says so if the sizes do not match.

**Coming from an earlier version?** `index.conf` is now `.env`, and `EMBEDDING_PROVIDER`, `OLLAMA_URL` and `OPENAI_API_KEY` were replaced by the three `EMBEDDING_*` settings above. The scripts warn if they still find the old names.

## Regenerating the export yourself

You only need this if you don't trust the committed `data/grundschutzpp-controls.jsonl` or want to point it at a different catalog edition. `export.py` is pure stdlib (no install step) and reads the OSCAL catalog plus three BSI-provided auxiliary files (`basethreats.csv`, `tags.csv`, the old↔new mapping JSON) from a local sibling checkout of [`BSI-Bund/Stand-der-Technik-Bibliothek`](https://github.com/BSI-Bund/Stand-der-Technik-Bibliothek) by default, same layout as [`grundschutz-docs/docs`](https://github.com/grundschutz-docs/docs). Alternatively set the `GRUNDSCHUTZPP_*` variables (see `.env.example`), the same convention `.github/workflows/export.yml` uses in CI.

```bash
python3 export.py
```

## What this is not

- It is not a finished chatbot, it is the data foundation for one. There is no demo.
- The search returns similar requirements, not answers. Whatever an assistant built on it says is not a statement by the BSI and not legal advice. The original catalog is always authoritative.

## Licensing

- **Code** in this repo: MIT (see `LICENSE`).
- **Catalog content** in `data/grundschutzpp-controls.jsonl` (`statement`, `guidance`, titles, tag hints, threat labels): **CC BY-SA 4.0, © BSI-Bund**, per the upstream catalog's own license — same as [`grundschutz-docs/docs`](https://github.com/grundschutz-docs/docs#license). If you build something on top of this data, attribute the BSI and keep the same license on derivatives of the catalog text itself.
- **Old IT-Grundschutz-Kompendium**: not reproduced here in any form. The [BSI's terms of use](https://www.bsi.bund.de/DE/Service/Nutzungsbedingungen/Nutzungsbedingungen_node.html) allow its free downloads only for non-commercial purposes and require the BSI's prior consent for any further use. `predecessors` therefore carries only the old requirement's ID, its relationship to the new one, and a link to the BSI's own PDF.

This project is unofficial and independent — not affiliated with, endorsed by, or produced by the Bundesamt für Sicherheit in der Informationstechnik (BSI).

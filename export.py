#!/usr/bin/env python3
"""
Reads the Grundschutz++ resolved OSCAL catalog and writes one JSON object
per control to data/grundschutzpp-controls.jsonl -- plain text + metadata,
no embeddings. Mirrors the accessor logic in grundschutz-docs/docs'
scripts/generate_docs.py (same env var names, same OSCAL field names), but
returns data instead of rendering MDX.

Pure stdlib, no dependencies -- only needed if you want to regenerate the
export yourself; the committed data/grundschutzpp-controls.jsonl is kept
in sync daily by .github/workflows/export.yml.

    python3 export.py
"""
import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SIBLING_ROOT = REPO_ROOT.parent  # same layout as grundschutz-docs/docs: catalog repo checked out next to this one

DEFAULT_CATALOG_FILE = (
    SIBLING_ROOT / "Grundschutz-PlusPlus" / "control_layer" / "Grundschutz++" / "Grundschutz++-resolved_catalog.json"
)
DEFAULT_BASETHREATS_FILE = SIBLING_ROOT / "Grundschutz-PlusPlus" / "documentation" / "namespaces" / "basethreats.csv"
DEFAULT_TAGS_FILE = SIBLING_ROOT / "Grundschutz-PlusPlus" / "documentation" / "namespaces" / "tags.csv"
DEFAULT_MAPPING_FILE = (
    SIBLING_ROOT / "Grundschutz-PlusPlus" / "control_layer" / "Mappings" / "IT-GS2023-zu-GSpp" / "ITGS-to-GS++-mapping_collection.json"
)
DEFAULT_BAUSTEINE_LINKS_FILE = REPO_ROOT / "data" / "bsi-kompendium-2023-bausteine.json"
DEFAULT_OUT_FILE = REPO_ROOT / "data" / "grundschutzpp-controls.jsonl"

CATALOG_FILE = Path(os.environ.get("GRUNDSCHUTZPP_CATALOG", DEFAULT_CATALOG_FILE))
BASETHREATS_FILE = Path(os.environ.get("GRUNDSCHUTZPP_BASETHREATS", DEFAULT_BASETHREATS_FILE))
TAGS_FILE = Path(os.environ.get("GRUNDSCHUTZPP_TAGS", DEFAULT_TAGS_FILE))
MAPPING_FILE = Path(os.environ.get("GRUNDSCHUTZPP_MAPPING", DEFAULT_MAPPING_FILE))
BAUSTEINE_LINKS_FILE = Path(os.environ.get("BSI_BAUSTEINE_LINKS", DEFAULT_BAUSTEINE_LINKS_FILE))
OUT_FILE = Path(os.environ.get("GRUNDSCHUTZPP_RAG_OUT", DEFAULT_OUT_FILE))
SITE_URL = os.environ.get("SITE_URL", "https://grundschutz-docs.de").rstrip("/")

PARAM_RE = re.compile(r"\{\{\s*insert:\s*param,\s*([a-zA-Z0-9._-]+)\s*\}\}")

LICENSE_NOTICE = "CC BY-SA 4.0 © BSI-Bund"
CATALOG_REPO = "BSI-Bund/Stand-der-Technik-Bibliothek"


# --- OSCAL accessors -- same field names/semantics as generate_docs.py -----

def prop(node, name):
    for p in node.get("props", []):
        if p.get("name") == name:
            return p
    return None


def prop_value(node, name):
    p = prop(node, name)
    return p.get("value") if p else None


def part_text(control, name):
    for part in control.get("parts", []):
        if part.get("name") == name:
            return part.get("prose", "")
    return None


def part_prop_value(control, part_name, prop_name):
    for part in control.get("parts", []):
        if part.get("name") == part_name:
            for p in part.get("props", []):
                if p.get("name") == prop_name:
                    return p.get("value")
    return None


def substitute_params(text, params_by_id):
    # Unlike generate_docs.py's version, no MDX-escaping here -- this output
    # is plain text/JSON for embeddings, not MDX source.
    if not text:
        return text

    def repl(m):
        pid = m.group(1)
        label = params_by_id.get(pid, pid)
        return f"[{label}]"

    return PARAM_RE.sub(repl, text)


def build_params_index(catalog):
    index = {}

    def walk(node):
        for p in node.get("params", []):
            index[p["id"]] = p.get("label", p["id"])
        for c in node.get("controls", []):
            walk(c)
        for g in node.get("groups", []):
            walk(g)

    for g in catalog["groups"]:
        walk(g)
    return index


def collect_controls(node):
    """Every control node under a control or group node, recursive.

    Deliberately includes **every** node, not just leaves -- in this
    catalog, parent nodes carry their own meaningful `statement` too (see
    generate_docs.py's collect_controls() docstring: counted against the
    real catalog, all control nodes have their own statement+modal_verb,
    including the ones with sub-requirements). Filtering to leaves only
    would silently drop real MUSS/SOLLTE/KANN requirements.
    """
    for c in node.get("controls", []):
        yield c
        yield from collect_controls(c)
    for g in node.get("groups", []):
        yield from collect_controls(g)


def load_basethreats():
    with BASETHREATS_FILE.open(newline="", encoding="utf-8") as f:
        return {row["ID"]: row["Begriff"] for row in csv.DictReader(f)}


def threats_for_control(control, basethreats_by_id):
    value = prop_value(control, "threats")
    if not value:
        return []
    ids = [t.strip() for t in value.split(",") if t.strip()]
    return [{"id": tid, "label": basethreats_by_id.get(tid)} for tid in ids]


def load_tags():
    with TAGS_FILE.open(newline="", encoding="utf-8") as f:
        return {row["Tag"]: row["Bedeutung"] for row in csv.DictReader(f)}


# Same hand-curated synonym list as generate_docs.py's TAG_SYNONYMS, kept
# in sync manually -- small enough (~10 entries) that duplicating beats
# depending on the other repo's internals.
TAG_SYNONYMS = {
    "Lieferketten": "Lieferanten, Zulieferer, Dienstleister, Supply Chain",
    "Hochverfügbarkeit": "Ausfallsicherheit, Redundanz",
    "Password Policy": "Passwortrichtlinie, Passwortregeln",
    "Insider Threat": "interne Bedrohung, Innentäter",
    "Bring Your Own Device": "BYOD, private Geräte, eigene Geräte",
    "Compliance-Management": "Regelkonformität, Vorschrifteneinhaltung",
    "Physical Access Control": "Zutrittskontrolle, Gebäudesicherheit",
    "Advanced Persistent Threats (APT)": "gezielter Angriff, staatlich gesteuerter Angreifer",
    "Exit-Strategie": "Anbieterwechsel, Vendor Lock-in, Vertragsende Dienstleister",
    "Kompetenzmanagement": "Schulung, Weiterbildung, Qualifikation",
}


def tags_for_control(control, tags_by_id):
    value = prop_value(control, "tags")
    if not value:
        return []
    names = [t.strip() for t in value.split(",") if t.strip()]
    return [
        {"name": name, "hint": tags_by_id.get(name), "synonyms": TAG_SYNONYMS.get(name)}
        for name in names
    ]


def build_control_index(catalog):
    """control-id -> {group_id, group_title, slug} of the group page it lives on."""
    index = {}

    def walk(node, group_id, group_title, slug):
        for control in node.get("controls", []) or []:
            index[control["id"]] = {"group_id": group_id, "group_title": group_title, "slug": slug}
            walk(control, group_id, group_title, slug)

    for group in catalog.get("groups", []) or []:
        slug = group["id"].lower()
        walk(group, group["id"], group["title"], slug)
        for subgroup in group.get("groups", []) or []:
            walk(subgroup, group["id"], group["title"], slug)
    return index


def build_predecessor_index(mapping_data):
    index = {}
    for group in mapping_data["mapping-collection"]["mappings"]:
        for m in group.get("maps", []):
            rel = m.get("relationship")
            sources = m.get("sources", [])
            targets = m.get("targets", [])
            if not sources or not targets or not rel:
                continue
            old_id = sources[0].get("id-ref")
            new_id = targets[0].get("id-ref")
            if not old_id or not new_id:
                continue
            index.setdefault(new_id, []).append((old_id, rel))
    return index


def baustein_id_from_itgs_id(itgs_id):
    m = re.match(r"^([A-Z]+(?:\.[0-9]+)+)\.A", itgs_id)
    return m.group(1) if m else None


def predecessors_for_control(control_id, predecessor_index, baustein_links):
    """Old-Kompendium predecessor relationships: IDs/relationship/PDF link only --
    never the old Kompendium's full text (unclear license, see
    grundschutz-docs/docs' adr/0005-vorgaenger-anforderung-alt-neu.md)."""
    entries = predecessor_index.get(control_id)
    if not entries:
        return []
    result = []
    for old_id, rel in entries:
        baustein_id = baustein_id_from_itgs_id(old_id)
        link = baustein_links.get(baustein_id) if baustein_id else None
        result.append({"old_id": old_id, "relationship": rel, "pdf_url": link})
    return result


def catalog_source(catalog):
    meta = catalog.get("metadata") or {}
    doc_ids = meta.get("document-ids") or []
    return {
        "title": meta.get("title") or "Anwenderkatalog Grundschutz++",
        "version": (meta.get("version") or "")[:10],
        "document_id": doc_ids[0].get("identifier") if doc_ids else None,
    }


def build_text(control_id, title, modal_verb, statement, guidance):
    parts = [f"{control_id} – {title}"]
    if modal_verb:
        parts.append(modal_verb)
    if statement:
        parts.append(statement)
    if guidance:
        parts.append(guidance)
    return "\n\n".join(parts)


def build_record(control, control_index, params_by_id, basethreats_by_id, tags_by_id, predecessor_index, baustein_links, generated_at):
    control_id = control["id"]
    title = control.get("title", "")
    modal_verb = part_prop_value(control, "statement", "modal_verb")
    statement = substitute_params(part_text(control, "statement"), params_by_id)
    guidance = substitute_params(part_text(control, "guidance"), params_by_id)
    loc = control_index.get(control_id, {})
    group_id = loc.get("group_id")
    group_title = loc.get("group_title")
    slug = loc.get("slug")

    return {
        "id": control_id,
        "title": title,
        "group_id": group_id,
        "group_title": group_title,
        "modal_verb": modal_verb,
        "sec_level": prop_value(control, "sec_level"),
        "effort_level": prop_value(control, "effort_level"),
        "tags": tags_for_control(control, tags_by_id),
        "threats": threats_for_control(control, basethreats_by_id),
        "predecessors": predecessors_for_control(control_id, predecessor_index, baustein_links),
        "statement": statement,
        "guidance": guidance,
        "text": build_text(control_id, title, modal_verb, statement, guidance),
        "source_url": f"{SITE_URL}/grundschutzpp/{slug}/#{control_id}" if slug else None,
        "license": LICENSE_NOTICE,
        "catalog_source": CATALOG_REPO,
        "generated_at": generated_at,
    }


def export():
    data = json.loads(CATALOG_FILE.read_text())
    catalog = data["catalog"]
    params_by_id = build_params_index(catalog)
    control_index = build_control_index(catalog)
    basethreats_by_id = load_basethreats()
    tags_by_id = load_tags()
    mapping_data = json.loads(MAPPING_FILE.read_text())
    predecessor_index = build_predecessor_index(mapping_data)
    baustein_links = json.loads(BAUSTEINE_LINKS_FILE.read_text())["bausteine"]
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with OUT_FILE.open("w", encoding="utf-8") as f:
        for group in catalog.get("groups", []):
            for control in collect_controls(group):
                record = build_record(
                    control, control_index, params_by_id, basethreats_by_id,
                    tags_by_id, predecessor_index, baustein_links, generated_at,
                )
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

    src = catalog_source(catalog)
    print(f"Wrote {count} controls to {OUT_FILE}")
    print(f"Catalog: {src['title']} ({src['version'] or 'no version'}, {src['document_id'] or 'no document id'})")
    return count


if __name__ == "__main__":
    export()

"""
Builds a Cytoscape.js-compatible elements object:
    {"nodes": [{"data": {...}}, ...], "edges": [{"data": {...}}, ...]}

Node types (colored differently on the frontend): gene, protein, disease,
pathway, organism, chemical, snp, and one "database" node per external
source that returned live/cached data for an entity — this is what lets
the graph branch outward the way section 5.5 of the project brief
describes (disease -> gene -> UniProt -> PDB structure, etc).
"""


def build_graph(entities: list, relationships: list, db_records_by_entity: dict) -> dict:
    """
    entities: list of models.Entity (SQLAlchemy rows, already committed)
    relationships: list of models.EntityRelationship rows
    db_records_by_entity: {entity_id: [models.DatabaseRecord, ...]}
    """
    nodes = []
    edges = []
    seen_db_nodes = set()

    for e in entities:
        nodes.append({"data": {
            "id": e.id,
            "label": e.text,
            "type": e.entity_type,
            "normalized_id": e.normalized_id,
            "confidence": e.confidence,
        }})

        for rec in db_records_by_entity.get(e.id, []):
            if rec.status not in ("ok", "offline_fallback"):
                continue
            db_node_id = f"db::{rec.source}::{e.normalized_id or e.text}"
            if db_node_id not in seen_db_nodes:
                seen_db_nodes.add(db_node_id)
                nodes.append({"data": {
                    "id": db_node_id,
                    "label": _source_label(rec.source),
                    "type": "database",
                    "source": rec.source,
                }})
            edges.append({"data": {
                "id": f"edge::{e.id}::{db_node_id}",
                "source": e.id,
                "target": db_node_id,
                "label": "linked in",
                "edge_type": "database_link",
            }})

    for r in relationships:
        edges.append({"data": {
            "id": r.id,
            "source": r.source_entity_id,
            "target": r.target_entity_id,
            "label": r.relation_type.replace("_", " "),
            "edge_type": "relationship",
        }})

    return {"elements": {"nodes": nodes, "edges": edges}}


_SOURCE_LABELS = {
    "ncbi_gene": "NCBI Gene",
    "uniprot": "UniProt",
    "pdb": "PDB",
    "string": "STRING",
    "go": "Gene Ontology",
    "kegg": "KEGG",
    "clinvar": "ClinVar",
}


def _source_label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source)

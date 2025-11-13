import os
import json
import uuid
from typing import List, Dict, Any
import boto3
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from pinecone import Pinecone

# --- 1. Load env ---------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
ASSESSMENT_ID = os.getenv("AUDIT_MANAGER_ASSESSMENT_ID")
ASSESSMENT_LABEL = os.getenv("AUDIT_MANAGER_ASSESSMENT_LABEL", ASSESSMENT_ID or "unknown-assessment")
FRAMEWORK = os.getenv("AUDIT_MANAGER_FRAMEWORK", "SOC2")

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX = os.getenv("PINECONE_INDEX", "quickstart")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "kb")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

if not all([AWS_REGION, ASSESSMENT_ID, PINECONE_API_KEY]):
    raise RuntimeError(
        "Missing one of AWS_REGION, AUDIT_MANAGER_ASSESSMENT_ID, or PINECONE_API_KEY. "
        "Check your .env / Streamlit secrets."
    )

# --- 2. Set up clients ---------------------------------------------------

audit = boto3.client("auditmanager", region_name=AWS_REGION)
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(PINECONE_INDEX)
encoder = SentenceTransformer(EMBEDDING_MODEL)


# --- 3. Helpers ----------------------------------------------------------

def list_assessments() -> List[Dict[str, Any]]:
    """Utility: list assessments (for debugging / discovery)."""
    out = []
    paginator = audit.get_paginator("list_assessments")
    for page in paginator.paginate():
        out.extend(page.get("assessmentMetadata", []))
    return out


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
ASSESSMENT_FRAMEWORK = os.getenv("FRAMEWORK", "SOC2")

def fetch_evidence_for_assessment(assessment_id: str) -> list[dict]:
    """
    Pulls evidence *folders* for a given Audit Manager assessment and turns
    them into RAG-ready rows for Pinecone.

    We do NOT use a paginator here because get_evidence_folders_by_assessment
    is not pageable in boto3. Instead we loop manually on nextToken.
    """
    audit = boto3.client("auditmanager", region_name=AWS_REGION)

    rows: list[dict] = []
    next_token = None

    while True:
        params = {
            "assessmentId": assessment_id,
            "maxResults": 1000,
        }
        if next_token:
            params["nextToken"] = next_token

        resp = audit.get_evidence_folders_by_assessment(**params)
        folders = resp.get("evidenceFolders", [])

        for folder in folders:
            control_id = folder.get("controlId")
            name = folder.get("name")
            data_source = folder.get("dataSource")
            total_evidence = folder.get("totalEvidence")
            selected_for_report = folder.get("assessmentReportSelectionCount")

            text_bits = [
                f"Assessment evidence folder '{name}' for control {control_id or 'unknown'}",
            ]
            if data_source:
                text_bits.append(f"data source: {data_source}")
            if total_evidence is not None:
                text_bits.append(f"total evidence items: {total_evidence}")
            if selected_for_report is not None:
                text_bits.append(
                    f"selected for assessment report: {selected_for_report} items"
                )

            text = ". ".join(text_bits)

            rows.append(
                {
                    "id": str(uuid.uuid4()),              # 🔑 give every row a stable ID
                    "text": text,
                    "framework": FRAMEWORK,    # e.g. "SOC2"
                    "control_id": control_id,
                    "assessment_id": assessment_id,
                    "evidence_keys": [],                  # can wire real S3 keys later
                }
            )

        next_token = resp.get("nextToken")
        if not next_token:
            break

    return rows

def sanitize_meta(md: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure Pinecone metadata only has JSON-serializable primitives."""
    cleaned = {}
    for k, v in md.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            cleaned[k] = v
        elif isinstance(v, list):
            # Keep only primitive lists
            cleaned[k] = [x for x in v if isinstance(x, (str, int, float, bool))]
        else:
            cleaned[k] = str(v)
    return cleaned


def chunker(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


# --- 4. Main ingest ------------------------------------------------------

def main():
    print(f"[AuditManager] Region={AWS_REGION}")
    print(f"[AuditManager] AssessmentId={ASSESSMENT_ID} (label={ASSESSMENT_LABEL})")

    # Optional: show what assessments exist (for debugging)
    # assessments = list_assessments()
    # print(json.dumps(assessments, indent=2, default=str))

    rows = fetch_evidence_for_assessment(ASSESSMENT_ID)
    if not rows:
        print("No evidence rows found for this assessment. Check that it has evidence collected.")
        return

    print(f"Fetched {len(rows)} evidence items from Audit Manager.")

    texts = [r["text"] for r in rows]
    print(f"Encoding {len(texts)} chunks with {EMBEDDING_MODEL} …")
    vecs = encoder.encode(texts, normalize_embeddings=True)

    payload = []
    for r, v in zip(rows, vecs):
        meta = sanitize_meta(
            {
                "text": r["text"][:5000],
                "framework": r["framework"],
                "control_id": r.get("control_id") or "",
                "assessment_id": r.get("assessment_id") or "",
                "source": r.get("source", ""),
                "evidence_keys": r.get("evidence_keys") or [],
            }
        )
        payload.append(
            {
                "id": str(r["id"]),
                "values": v.tolist(),
                "metadata": meta,
            }
        )

    print(f"Upserting to Pinecone index={PINECONE_INDEX} namespace={PINECONE_NAMESPACE} …")
    for part in chunker(payload, 100):
        index.upsert(vectors=part, namespace=PINECONE_NAMESPACE)

    stats = index.describe_index_stats()
    stats_dict = stats.to_dict() if hasattr(stats, "to_dict") else stats
    print("Index stats after ingest:")
    print(json.dumps(stats_dict, indent=2, default=str))


if __name__ == "__main__":
    main()

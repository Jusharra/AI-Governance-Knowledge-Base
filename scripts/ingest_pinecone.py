# scripts/ingest_pinecone.py
import os
import json
import uuid
import glob
from pathlib import Path
from typing import Dict, Any, Iterable, List

from tqdm import tqdm
from sentence_transformers import SentenceTransformer
from pinecone import Pinecone, ServerlessSpec

# ------------------
# Config (env first)
# ------------------
INDEX   = os.getenv("PINECONE_INDEX", "quickstart")
NS      = os.getenv("PINECONE_NAMESPACE", "kb")
MODEL   = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")  # 384-dim
CLOUD   = os.getenv("PINECONE_CLOUD", "aws")
REGION  = os.getenv("PINECONE_REGION", "us-east-1")

API_KEY = os.getenv("PINECONE_API_KEY")
if not API_KEY:
    raise SystemExit("PINECONE_API_KEY not set")

pc = Pinecone(api_key=API_KEY)

# Ensure index exists (idempotent)
def ensure_index(index_name: str, dim: int = 384, metric: str = "cosine") -> None:
    try:
        # Describe to test existence; will raise if not found
        _ = pc.Index(index_name).describe_index_stats()
    except Exception:
        pc.create_index(
            name=index_name,
            dimension=dim,
            metric=metric,
            spec=ServerlessSpec(cloud=CLOUD, region=REGION),
        )

ensure_index(INDEX)
ix = pc.Index(INDEX)

enc = SentenceTransformer(MODEL)

# ------------------
# Helpers
# ------------------
def sanitize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Pinecone metadata must be str | number | bool | list[str]. Drop/convert others."""
    safe: Dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (str, bool, int, float)):
            safe[k] = v
        elif isinstance(v, list):
            safe[k] = [str(x) for x in v if x is not None]
        else:
            safe[k] = str(v)
    return safe

def chunker(seq: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i:i+size]

# ------------------
# Load content
# ------------------
def load_chunks() -> List[Dict[str, Any]]:
    """
    Reads RAG chunks from:
      - data/kb.jsonl  (preferred; one JSON object per line)
      - data/*.md      (fallback; each file becomes one chunk)
    Normalizes fields and guarantees required keys.
    """
    rows: List[Dict[str, Any]] = []
    jsonl = Path("data/kb.jsonl")

    if jsonl.exists():
        with jsonl.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    rows.append(obj)
                except Exception:
                    # skip bad line silently
                    pass
    else:
        for fp in glob.glob("data/*.md"):
            with open(fp, "r", encoding="utf-8") as f:
                text = f.read()
            rows.append({
                "text": text,
                "framework": "POL",
                "policy_id": os.path.basename(fp)
            })

    norm: List[Dict[str, Any]] = []
    for r in rows:
        txt = r.get("text")
        if not txt:
            continue
        norm.append({
            "id": str(r.get("id") or uuid.uuid4()),
            "text": str(txt),
            "framework": r.get("framework", "POL") or "POL",
            # NEVER leave these as None; Pinecone rejects null
            "control_id": r.get("control_id") or "",
            "policy_id": r.get("policy_id") or "",
            "source": r.get("source") or "",
        })
    return norm

# ------------------
# Main
# ------------------
def main() -> None:
    rows = load_chunks()
    if not rows:
        print("No content found to ingest. Put chunks in data/kb.jsonl or data/*.md")
        return

    texts = [r["text"] for r in rows]
    print(f"Encoding {len(texts)} chunks with {MODEL}…")
    vecs = enc.encode(texts, normalize_embeddings=True)

    payload = []
    ASSESS_ID = os.getenv("AUDIT_MANAGER_ASSESSMENT_ID", "")

    for r, v in zip(rows, vecs):
        meta = sanitize_meta({
            "text": (r.get("text") or "")[:5000],          # cap & guard
            "framework": r.get("framework", "POL"),
            "control_id": r.get("control_id") or "",
            "policy_id": r.get("policy_id") or "",
            "assessment_id": ASSESS_ID,                   # link to your assessment
            "evidence_keys": r.get("evidence_keys") or [], # list of S3 keys (if you have them)
            "s3_key": r.get("s3_key") or "",               # optional single S3 object key
            "source": r.get("source") or "",               # provenance (file, export, etc.)
        })

        payload.append({
            "id": str(r.get("id") or uuid.uuid4()),
            "values": v.tolist(),
            "metadata": meta,
        })

    print(f"Upserting to Pinecone index={INDEX} namespace={NS} …")
    for part in tqdm(list(chunker(payload, 100))):
        ix.upsert(vectors=part, namespace=NS)

    # Show stats for this namespace (handle SDK objects safely)
    stats = ix.describe_index_stats()
    if hasattr(stats, "to_dict"):
        stats_dict = stats.to_dict()
    elif isinstance(stats, dict):
        stats_dict = stats
    else:
        # last-resort stringify for anything exotic
        stats_dict = json.loads(json.dumps(stats, default=str))

    print("Index stats:", json.dumps(stats_dict, indent=2))
    ns_count = stats_dict.get("namespaces", {}).get(NS, {}).get("vector_count", 0)
    print(f"Namespace '{NS}' vector_count =", ns_count)

if __name__ == "__main__":
    main()

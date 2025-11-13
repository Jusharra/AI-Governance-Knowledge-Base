# app/retriever.py
import os, numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# Try repo-root/.env first, then walk up from CWD as a fallback
repo_root = Path(__file__).resolve().parents[1]
explicit_env = repo_root / ".env"
env_path = str(explicit_env) if explicit_env.exists() else find_dotenv(".env", usecwd=True)
load_dotenv(env_path, override=True)


# Toggle by .env: VECTOR_STORE=faiss|pinecone
_BACKEND = os.getenv("VECTOR_STORE", "faiss").lower()
_MODEL   = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# Lazy imports so the other path doesn’t force deps
pc = None
faiss = None

class Retriever:
    def __init__(self, k: int = 5):
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")   # CPU only
        self.k = k
        self.model = SentenceTransformer(_MODEL, device="cpu")

        if _BACKEND == "pinecone":
            # Pinecone client & index
            from pinecone import Pinecone
            global pc
            pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
            self.index_name = os.getenv("PINECONE_INDEX", "quickstart")
            self.namespace  = os.getenv("PINECONE_NAMESPACE", "")
            self.index = pc.Index(self.index_name)
            # No local index to load
        else:
            # FAISS fallback (local file)
            global faiss
            import faiss
            self.faiss = faiss
            idx_path = Path("data") / "faiss.index"
            meta_path = Path("data") / "faiss_meta.json"
            if not idx_path.exists() or not meta_path.exists():
                raise RuntimeError("FAISS index not built. Set VECTOR_STORE=pinecone or build FAISS.")
            import json
            self.index = faiss.read_index(str(idx_path))
            with open(meta_path, "r") as f:
                self.meta = json.load(f)  # list of doc chunks (dicts)

    def _embed(self, texts):
        vecs = self.model.encode(texts, normalize_embeddings=True)
        return np.asarray(vecs, dtype="float32")

    def search(self, query: str):
        q = self._embed([query])[0]
        if _BACKEND == "pinecone":
            # Pinecone query (uses namespace if provided)
            res = self.index.query(
                namespace=self.namespace or None,
                vector=q.tolist(),
                top_k=self.k,
                include_metadata=True
            )
            hits = []
            for m in res.matches or []:
                md = m.metadata or {}
                hits.append({
                    "text": md.get("text",""),
                    "framework": md.get("framework","POL"),
                    "control_id": md.get("control_id") or md.get("policy_id"),
                    "policy_id": md.get("policy_id"),
                    "score": float(m.score)
                })
            return hits
        else:
            # FAISS
            D, I = self.index.search(q.reshape(1, -1), self.k)
            hits = []
            for pos, idx in enumerate(I[0]):
                if idx < 0: continue
                rec = self.meta[idx]
                hits.append({
                    "text": rec["text"],
                    "framework": rec.get("framework","POL"),
                    "control_id": rec.get("control_id"),
                    "policy_id": rec.get("policy_id"),
                    "score": float(1.0 - D[0][pos])  # optional re-score
                })
            return hits

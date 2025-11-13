import os, json, pandas as pd, numpy as np
from sentence_transformers import SentenceTransformer
import faiss, pathlib

DATA_DIR = "data"
VEC_DIR = "vectors"
os.makedirs(VEC_DIR, exist_ok=True)

def load_frames():
    dfs = []
    for f in ["controls_soc2.csv","controls_nist80053.csv","controls_iso42001.csv","policies_internal.csv"]:
        path = pathlib.Path(DATA_DIR)/f
        df = pd.read_csv(path)
        df["source_file"] = f
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)

def build_corpus_row(r):
    # normalize a unified text field for embedding
    if "control_id" in r and not pd.isna(r["control_id"]):
        head = f'{r["framework"]}:{r["control_id"]} {r["title"]}'
    else:
        head = f'POLICY:{r["policy_id"]} {r["title"]}'
    return head + " — " + r["text"]

def main():
    df = load_frames()
    df["corpus"] = df.apply(build_corpus_row, axis=1)
    model = SentenceTransformer("all-MiniLM-L6-v2")
    embs = model.encode(df["corpus"].tolist(), normalize_embeddings=True)
    # Save FAISS + metadata
    dim = embs.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(np.array(embs, dtype=np.float32))
    faiss.write_index(index, f"{VEC_DIR}/kb.faiss")
    df.to_json(f"{VEC_DIR}/kb_meta.json", orient="records", force_ascii=False, indent=2)
    print(f"Indexed {len(df)} items → vectors/kb.faiss")

if __name__ == "__main__":
    main()

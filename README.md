# 📘 **AI Governance Knowledge Base — Compliance RAG + Pinecone Assistant (MCP Edition)**

*A dual-engine AI system for policy, audit, and control intelligence.*

---

## 🚀 Overview

This project implements a **full-stack AI Governance & Compliance Intelligence system** combining:

### **1️⃣ Pinecone Dense Vector RAG**

Your internal evidence & policy store
→ Fast, structured, deterministic retrieval of control text, policies, and mappings.

### **2️⃣ Pinecone Assistant via MCP (Model Context Protocol)**

Your Pinecone-hosted compliance intelligence agent
→ Interprets, synthesizes, and cites information from your uploaded files (PDFs, DOCX, NIST, ISO, AIMS, AI governance docs, etc.)

### **3️⃣ Streamlit Front-End**

A dual-mode interface allowing users to choose:

| Mode                          | Description                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------------------- |
| **KB RAG (Pinecone Vectors)** | Pulls chunks from your vector DB; deterministic and evidence-grounded                       |
| **Pinecone Assistant (MCP)**  | Uses Pinecone Assistant as an AI auditor, retrieving & synthesizing from uploaded documents |

---

## ✨ Key Features

### 🔹 **Policy & Control Retrieval (Vector RAG)**

* Ingests policies, mappings, CSVs, and guidance files
* Embeds using SentenceTransformers
* Stores dense vectors in Pinecone
* Metadata includes:

  * `framework`, `control_id`, `policy_id`
  * `evidence_keys`, `source_uri`
  * `assessment_id` (Audit Manager alignment)

### 🔹 **Pinecone Assistant (MCP Integration)**

The app integrates directly with a Pinecone Assistant using:

```
https://<HOST>/mcp/assistants/<ASSISTANT_NAME>
```

This adds:

* AI reasoning over uploaded compliance PDFs/DOCX
* Rich synthesis + citations
* MCP Tools for context retrieval
* Integration with ReAct agents via LangChain

### 🔹 **Dual Retrieval Engine**

The user selects:

| Mode              | Best For                                                                         |
| ----------------- | -------------------------------------------------------------------------------- |
| **RAG (Vectors)** | Deterministic policy lookup, control mapping, evidence keys                      |
| **MCP Assistant** | Narrative compliance answers, audit-ready summaries, synthesized interpretations |

### 🔹 **Evidence-Aware Retrieval**

* Retrieves S3 evidence via presigned URLs
* Uses `evidence_map.json` or Pinecone metadata
* Grounded answers + evidence references
* Fully hashed, immutable audit records

### 🔹 **S3 Audit Snapshot Logging**

Every interaction logs:

* Query
* Vector IDs
* Scores
* Metadata
* Evidence references
* Timestamp
* Hash for integrity

Uploaded securely to your S3 audit bucket.

---

## 🧠 Architecture

```
Streamlit UI
   │
   ├── Mode A: Vector RAG
   │       ├── Ingest local CSV/MD/JSONL
   │       ├── Embed chunks → Pinecone
   │       ├── Filter by metadata (framework, control_id)
   │       └── Join evidence keys → S3 URLs
   │
   └── Mode B: MCP Assistant
           ├── MultiServerMCPClient
           ├── ReAct agent with Claude 3.7 Sonnet
           ├── Pinecone Assistant MCP endpoint
           └── Uploaded documents (PDF/DOCX)
```

---

## 🗄️ File Structure

```
AI-Governance-Knowledge-Base/
│
├── app/
│   └── main.py            # Streamlit UI with dual-mode (RAG + MCP)
│
├── scripts/
│   ├── ingest_pinecone.py # Ingests vectors
│   ├── mcp_client.py      # MCP → Pinecone Assistant client
│   └── logger.py          # S3 snapshot logging
│
├── data/
│   ├── controls_iso42001.csv
│   ├── controls_soc2.csv
│   ├── evidence_map.json
│   ├── policies_internal.csv
│   └── *.md (optional)
│
├── vectors/
│   ├── kb.faiss           # Local fallback
│   └── kb_meta.json
│
├── .env                   # API keys + Pinecone + S3 + MCP config
└── README.md
```

---

## ⚙️ Installation

```bash
git clone https://github.com/<you>/AI-Governance-Knowledge-Base.git
cd AI-Governance-Knowledge-Base
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## 🔑 Environment Variables (`.env`)

```env
# Pinecone Vector RAG
PINECONE_API_KEY=
PINECONE_INDEX=quickstart
PINECONE_NAMESPACE=kb

# Pinecone Assistant (MCP)
PINECONE_ASSISTANT_HOST=
PINECONE_ASSISTANT_NAME=
PINECONE_MCP_URL=https://<HOST>/mcp/assistants/<NAME>

# LLM Provider
ANTHROPIC_API_KEY=

# AWS S3 for audit logs
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AUDIT_S3_BUCKET=
AUDIT_MANAGER_ASSESSMENT_ID=
```

---

## 📥 Ingesting Data into Pinecone

Place your documents in:

* `data/kb.jsonl` **or**
* any `.md` files in `data/*.md`

Run ingestion:

```bash
python scripts/ingest_pinecone.py
```

Verify:

```python
from pinecone import Pinecone
ix = pc.Index("quickstart")
print(ix.describe_index_stats())
```

---

## 🖥️ Running the App

```bash
streamlit run app/main.py
```

Then choose:

* **RAG (Pinecone Vectors)**
* **Pinecone Assistant (MCP)**

---

## 🌐 Adding a New Namespace (Partner Onboarding)

To onboard a new partner:

1. Create a namespace:

```python
ix.describe_index_stats()
# namespace auto-created on first upsert
```

2. Run ingest with:

```bash
PINECONE_NAMESPACE=partner123 python scripts/ingest_pinecone.py
```

3. Pass namespace through `main.py` when querying.

This gives you:

* Multi-partner isolation
* Zero cross-tenant data exposure
* Fast onboarding (minutes)

---

## 🧩 MCP Integration Details

We use:

```
langchain_mcp_adapters
langgraph
MultiServerMCPClient
Claude 3.7 Sonnet
```

Your MCP code:

```python
client = MultiServerMCPClient({
  "pinecone-kb": {
    "url": PINECONE_MCP_URL,
    "transport": "streamable_http",
    "headers": {"Authorization": f"Bearer {PINECONE_API_KEY}"}
  }
})
```

---

## 🧪 Example MCP Query

```
Which control covers Multifactor Authentication?
```

Assistant returns:

* Control mapping
* Direct quotes from PDF/DOCX
* Citations
* Clean narrative
* JSON-structured output

---

## 🛡️ Security Posture

✔ Evidence stored only in S3 (your bucket, your KMS).
✔ Pinecone stores **text only** + metadata (no evidence).
✔ Audit logs are hashed and immutable.
✔ MCP transport uses secure signed requests.
✔ Streamlit layer prevents prompt-injection.

---

## 🏁 Status

**Demo-ready**, **CISO-ready**, and **audit-ready** at:

Streamlit:https://ai-governance-knowledge-base.streamlit.app/
Notion: https://www.notion.so/Project-12-AI-Governance-Knowledge-Base-RAG-Agent-for-Policies-Controls-2a9f54e7005c80219187e30e2d31eb75?source=copy_link

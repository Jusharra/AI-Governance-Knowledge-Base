import os, json, streamlit as st
import sys
from pathlib import Path
from dotenv import load_dotenv
import streamlit as st
from retriever import Retriever
from guardrails import sanitize_query
from governance import check_model_governance
from logger import log_event, snapshot_to_s3, presign_many, presign_url
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic




# Try repo-root/.env first, then walk up from CWD as a fallback
repo_root = Path(__file__).resolve().parents[1]
explicit_env = repo_root / ".env"
env_path = str(explicit_env) if explicit_env.exists() else find_dotenv(".env", usecwd=True)
load_dotenv(env_path, override=True)


# Load .env so Streamlit sees AUDIT_S3_BUCKET, AWS_PROFILE, etc.
load_dotenv(dotenv_path=".env", override=False)

# ---------- Helpers ----------
def synthesize_answer(query, hits):
    """Very simple grounding: concat top chunks and return a naive confidence."""
    contexts = "\n\n".join([
        f"- [{h.get('framework','POL')}] {h.get('control_id', h.get('policy_id',''))}: {h['text']}"
        for h in hits
    ])
    answer = (
        f"**Grounded Answer:**\nGiven your question \"{query}\", here are relevant controls/policies:\n\n"
        f"{contexts}\n\nCitations: " + ", ".join(
            [f"{h.get('framework','POL')}:{h.get('control_id', h.get('policy_id',''))}" for h in hits]
        )
    )
    confidence = round(sum([h["score"] for h in hits]) / len(hits), 3) if hits else 0.0
    return answer, confidence


def load_evidence_map():
    try:
        with open("data/evidence_map.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def compute_ev_refs(hits, evidence_map):
    """
    Prefer evidence_keys from Pinecone metadata; fall back to evidence_map.json
    Returns: (ev_refs, looked_up_keys)
      ev_refs: [{control, s3_key, url, ...}]
      looked_up_keys: just the S3 keys we attempted to presign
    """
    ev_refs = []
    looked_up_keys = []

    for h in hits or []:
        md = h.get("metadata", {}) if isinstance(h, dict) else {}
        framework = md.get("framework", h.get("framework", "POL"))
        ctrl_id = md.get("control_id") or md.get("policy_id") or ""
        control_key = f"{framework}:{ctrl_id}".strip(":")

        # 1) Prefer Pinecone metadata
        keys = md.get("evidence_keys") or []
        if keys:
            looked_up_keys.extend(keys)
            for item in presign_many(keys):
                ev_refs.append({
                    "control": control_key,
                    "s3_key": item["key"],
                    "url": item["url"],
                })
            continue

        # 2) Fallback to evidence_map.json
        for e in evidence_map.get(control_key, []):
            s3_key = e.get("s3_key")
            if s3_key:
                looked_up_keys.append(s3_key)
                pres = presign_many([s3_key])
                url = pres[0]["url"] if pres and pres[0]["url"] else None
            else:
                url = None
            ev_refs.append({
                "control": control_key,
                "s3_key": s3_key,
                "url": url,
                **{k: v for k, v in e.items() if k != "s3_key"},
            })

    return ev_refs, looked_up_keys

def evidence_refs(hits):
    # 1) read from Pinecone metadata if present
    refs = []
    for h in hits:
        ek = (h.get("evidence_keys") or 
              h.get("metadata", {}).get("evidence_keys") or [])
        for key in ek:
            refs.append({"control": f"{h['framework']}:{h.get('control_id')}", 
                         "s3_key": key, "url": presign_url(key)})

    # 2) fallback: evidence_map.json
    if not refs:
        try:
            with open("data/evidence_map.json","r") as f:
                mapping = json.load(f)
            for h in hits:
                k = f"{h.get('framework','POL')}:{h.get('control_id',h.get('policy_id',''))}"
                for key in mapping.get(k, []):
                    refs.append({"control": k, "s3_key": key, "url": presign_url(key)})
        except FileNotFoundError:
            pass

    return refs
# ---------- MCP / Pinecone Assistant helpers ----------

async def _mcp_query_async(user_query: str) -> str:
    """
    Call Pinecone Assistant over MCP and return the LLM's final answer text.
    """
    # Make sure .env is loaded (safe to call multiple times)
    load_dotenv(".env")

    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    pinecone_host = os.getenv("PINECONE_ASSISTANT_HOST")
    pinecone_assistant = os.getenv("PINECONE_ASSISTANT_NAME")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    if not all([pinecone_api_key, pinecone_host, pinecone_assistant, anthropic_key]):
        return (
            "MCP mode misconfigured: missing one of "
            "PINECONE_API_KEY, PINECONE_ASSISTANT_HOST, "
            "PINECONE_ASSISTANT_NAME, ANTHROPIC_API_KEY in .env"
        )

    mcp_url = f"https://{pinecone_host}/mcp/assistants/{pinecone_assistant}"
    # Build MCP client
    client = MultiServerMCPClient(
        {
            "pinecone_assistant": {
                "url": mcp_url,
                "transport": "streamable_http",
                "headers": {
                    "Authorization": f"Bearer {pinecone_api_key}"
                },
            }
        }
    )

    # Load MCP tools from that assistant
    tools = await client.get_tools()

    # Anthropic model for tool-using agent
    model = ChatAnthropic(
        model_name="claude-3-7-sonnet-latest",
        api_key=anthropic_key,
    )

    # Let LangGraph build a ReAct-style agent that can call MCP tools
    agent = create_react_agent(model, tools)

    # Ask it to explicitly use the Pinecone assistant tools for context
    result = await agent.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Use the Pinecone Assistant MCP tools to answer this "
                        "question using its knowledge base only. "
                        f"Question: {user_query}"
                    ),
                }
            ]
        }
    )

    # LangGraph returns a dict with a messages list
    messages = result.get("messages", [])
    if not messages:
        return "MCP agent returned no messages."

    final_msg = messages[-1]
    content = getattr(final_msg, "content", final_msg)

    # Content can be list or string depending on stack; normalize
    if isinstance(content, list):
        parts = []
        for c in content:
            # LangChain content blocks often have .text
            if hasattr(c, "text"):
                parts.append(c.text)
            else:
                parts.append(str(c))
        return "\n".join(parts)

    return str(content)


def mcp_query(user_query: str) -> str:
    """
    Sync wrapper for Streamlit. Runs the async MCP query.
    """
    try:
        return asyncio.run(_mcp_query_async(user_query))
    except RuntimeError as e:
        # Edge case: if there's already an event loop running
        return f"MCP error: {e}"

# ---------- UI ----------
st.set_page_config(page_title="AI Governance KB", layout="wide")
st.title("AI Governance Knowledge Base (RAG for Policies & Controls)")
#st.caption(f"env={os.getenv('VECTOR_STORE')} | .env loaded from={env_path}")
st.caption("Ask: “Which control covers MFA?” or “Show evidence for CC6.6.”")

mode = st.radio(
    "Answer mode",
    ["KB RAG (Pinecone vectors)", "Pinecone Assistant (MCP)"],
    horizontal=True,
)


ok, msg = check_model_governance()
if not ok:
    st.error(f"Model governance policy violation: {msg}")

query = st.text_input("Your compliance question", key="query_input")

# Session defaults so variables always exist
defaults = {
    "hits": [],
    "answer": "",
    "conf": 0.0,
    "pii": {},
    "inj": {},
    "redacted": "",
    "ev_refs": [],
    "looked_up_keys": [],
}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)

# Build retriever
ret = Retriever(k=5)

# Ask -> run retrieval, build answer, compute evidence references
col1, col2 = st.columns([1, 1])
with col1:
    ask_clicked = st.button("Ask")
with col2:
    snapshot_clicked = st.button("Snapshot audit log to S3")

if ask_clicked and query:
    if mode == "KB RAG (Pinecone vectors)":
        # ---- Existing RAG path ----
        redacted, pii, inj = sanitize_query(query)
        if inj.get("is_injection"):
            st.warning(f"Potential prompt-injection detected: {inj['triggers']}")

        hits = ret.search(redacted)
        answer, conf = synthesize_answer(redacted, hits)

        evidence_map = load_evidence_map()
        ev_refs, looked_up_keys = compute_ev_refs(hits, evidence_map)

        st.session_state.update({
            "hits": hits,
            "answer": answer,
            "conf": conf,
            "pii": pii,
            "inj": inj,
            "redacted": redacted,
            "ev_refs": ev_refs,
            "looked_up_keys": looked_up_keys,
        })

    else:
        # ---- MCP / Pinecone Assistant path ----
        with st.spinner("Querying Pinecone Assistant via MCP…"):
            mcp_answer = mcp_query(query)

        st.session_state.update({
            "hits": [],
            "answer": mcp_answer,
            "conf": 0.0,
            "pii": {},
            "inj": {},
            "redacted": query,        # just store the raw text here
            "ev_refs": [],
            "looked_up_keys": [],
        })

# Snapshot -> upload audit log (UI always visible even if upload disabled)
if snapshot_clicked:
    loc = snapshot_to_s3()
    st.success(f"Uploaded: {loc}" if loc else "Set AUDIT_S3_BUCKET to enable.")

# ---------- Results (always shown after Ask) ----------
hits = st.session_state["hits"]
answer = st.session_state["answer"]
conf = st.session_state["conf"]
pii = st.session_state["pii"]
inj = st.session_state["inj"]
redacted = st.session_state["redacted"]
ev_refs = st.session_state["ev_refs"]
looked_up_keys = st.session_state["looked_up_keys"]

if answer:
    st.markdown(answer)
    st.metric("Confidence (sim mean)", conf)

    if pii:
        with st.expander("PII redactions detected"):
            st.json(pii)

    with st.expander("Retrieved chunks"):
        st.json(hits)

    # Evidence references: ALWAYS rendered (computed after Ask)
    with st.expander("Evidence references"):
        if ev_refs:
            st.json(ev_refs)
        else:
            st.write("No evidence references matched your retrieved controls.")

    # Optional debug to spot key mismatches
    if not ev_refs:
        evidence_map = load_evidence_map()
        with st.expander("Evidence (debug keys)"):
            st.write("Looked-up keys (from retrieval):")
            st.code("\n".join(looked_up_keys) if looked_up_keys else "(none)")
            st.write("Available keys in data/evidence_map.json:")
            st.code("\n".join(sorted(evidence_map.keys())) if evidence_map else "(none)")

    audit_id = log_event({
        "action": "qa",
        "query_raw": query,
        "query_redacted": redacted,
        "pii_findings": pii,
        "inj": inj,
        "retrieved": [{
            "id": h.get('control_id', h.get('policy_id','')),
            "framework": h.get('framework'),
            "score": h["score"]
        } for h in hits],
        "confidence": conf,
        "evidence": ev_refs,
        "model_used": os.getenv("BEDROCK_LLM_MODEL", "local")
    })
    st.caption(f"Audit entry hash: `{audit_id}`")

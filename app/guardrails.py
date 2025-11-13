import re, json, yaml
from pathlib import Path

PII = yaml.safe_load(Path("security/pii_patterns.yaml").read_text())
PI_RULES = [ (r["name"], re.compile(r["regex"], re.I)) for r in PII["patterns"] ]
INJ = yaml.safe_load(Path("security/prompt_injection_rules.yaml").read_text())
INJ_TERMS = [t.lower() for t in INJ["deny_if_contains"]]

def redact_pii(text: str):
    masked = text
    findings = []
    for name, rx in PI_RULES:
        def _repl(m):
            findings.append({"type":name,"match":m.group(0)})
            return f"[REDACTED:{name}]"
        masked = rx.sub(_repl, masked)
    return masked, findings

def detect_prompt_injection(text: str):
    lower = text.lower()
    hits = [t for t in INJ_TERMS if t in lower]
    return {"is_injection": len(hits)>0, "triggers": hits}

def sanitize_query(user_query: str):
    redacted, pii = redact_pii(user_query)
    inj = detect_prompt_injection(redacted)
    return redacted, pii, inj

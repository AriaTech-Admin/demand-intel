"""OpenCode AI provider (OpenAI-compatible).
Uses OPENCODE_API_KEY for derived insights as fallback when Gemini unavailable.
Endpoint auto-detected: tries api.opencode.ai and api.openai.com compatible paths."""
import json
import logging
import httpx

from .. import config

log = logging.getLogger(__name__)

# Try common bases; first successful wins
CANDIDATE_BASES = [
    "https://api.opencode.ai/v1",
    "https://api.opencode.ai",
]

def _available() -> bool:
    return bool(config.OPENCODE_API_KEY)

def generate_opencode_insight(title: str, type_: str, genres: list, overview: str | None) -> dict | None:
    if not _available():
        return None
    prompt = f"Summarize {type_} ''{title}'' genres {genres} overview {overview or "N/A"} in strict JSON with keys: summary, tags (2-4). No markdown."
    for base in CANDIDATE_BASES:
        url = base.rstrip("/") + "/chat/completions"
        try:
            with httpx.Client(timeout=15) as client:
                r = client.post(url, headers={"Authorization": f"Bearer {config.OPENCODE_API_KEY}", "Content-Type":"application/json"},
                                json={"model":"gpt-3.5-turbo", "messages":[{"role":"user","content":prompt}]})
                if r.status_code==200:
                    j=r.json()
                    txt=j["choices"][0]["message"]["content"]
                    if "summary" in txt:
                        try:
                            data=json.loads(txt.strip().strip("`"))
                            return {"summary": str(data.get("summary",""))[:300], "tags": data.get("tags",[]), "source":"OpenCode", "quality":"derived"}
                        except:
                            return {"summary": txt[:300], "tags": [], "source":"OpenCode", "quality":"derived"}
                log.info("OpenCode %s -> %s %s", base, r.status_code, r.text[:300])
        except Exception as e:
            log.warning("OpenCode %s failed %s", base, e)
    return None

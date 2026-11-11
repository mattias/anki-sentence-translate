"""Translation engines and the bold check. No Anki imports, so it can be tested outside Anki."""
import html
import json
import re
import urllib.error
import urllib.request

SYSTEM_PROMPT = """You translate Japanese example sentences from flashcards into English for a learner of Japanese.

Rules:
- Translate only the given sentence into natural, idiomatic English. Keep its tone (casual, rude, polite, childish) and keep it about as short as the original.
- If the sentence is a fragment, translate it as a fragment. Do not add explanations, notes, romaji or quotation marks around the whole sentence.
- The flashcard teaches one target word. In your English sentence, wrap the English word or short phrase that carries the meaning of the target word in exactly one <b>...</b> pair. Use no other HTML.
- Bold the English equivalent, never the Japanese word. If the target word has no single-word equivalent (a particle, sound effect or grammatical ending), bold the shortest phrase that expresses it.

Answer only with JSON: {"translation": "..."}"""

EXAMPLES = [
    ("竜は<b>左目</b>がつぶれてる。", "左目", "The dragon has its <b>left eye</b> crushed."),
    ("あ、黒部さん、こんなところで<b>奇遇</b>ですね。", "奇遇", "Ah, Mr. Kurobe, what a <b>coincidence</b> meeting you here."),
    ("なぜ掃除人に<b>変装</b>などを？", "変装", "Why are you <b>disguised</b> as a janitor?"),
]

SCHEMA = {
    "type": "object",
    "properties": {"translation": {"type": "string"}},
    "required": ["translation"],
    "additionalProperties": False,
}

BOLD_RE = re.compile(r"<b>(.+?)</b>", re.S | re.I)


USAGE = {}  # running token totals for the Claude engine (input_tokens, output_tokens)


class TranslateError(Exception):
    pass


def clean_sentence(raw):
    """Field HTML -> text, keeping <b> marks (the target word is usually bolded in Sentence)."""
    text = re.sub(r"<br\s*/?>|</div>\s*<div>", " ", raw or "", flags=re.I)
    text = re.sub(r"<(?!/?b>)[^>]*>", "", text, flags=re.I)
    text = re.sub(r"\[[^\]]*\]", "", text)  # furigana brackets, if any
    return html.unescape(text).replace("\xa0", " ").strip()


def plain(raw):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", raw or ""))).strip()


def user_message(sentence, expression, reading="", meaning=""):
    lines = [f"Japanese sentence: {sentence}", f"Target word: {expression}"]
    if reading:
        lines.append(f"Reading: {reading}")
    if meaning:
        lines.append(f"Dictionary meaning (hint only): {meaning[:300]}")
    return "\n".join(lines)


def build_messages(sentence, expression, reading="", meaning=""):
    msgs = []
    for jp, word, en in EXAMPLES:
        msgs.append({"role": "user", "content": user_message(jp, word)})
        msgs.append({"role": "assistant", "content": json.dumps({"translation": en}, ensure_ascii=False)})
    msgs.append({"role": "user", "content": user_message(sentence, expression, reading, meaning)})
    return msgs


def check(translation):
    """Return the cleaned translation, or raise if it does not have exactly one non-empty bold span."""
    t = (translation or "").strip()
    t = re.sub(r"<strong>", "<b>", t, flags=re.I)
    t = re.sub(r"</strong>", "</b>", t, flags=re.I)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)  # markdown bold from local models
    t = re.sub(r"<(?!/?b>)[^>]*>", "", t, flags=re.I).strip()
    if len(t) >= 2 and t[0] == t[-1] == '"':
        t = t[1:-1].strip()
    if t.lower().count("<b>") == 1 and "</b>" not in t.lower():
        # small local models sometimes forget the closing tag on the last word(s): close it before the end punctuation
        m = re.match(r"(.*<b>.*?)([.!?~…。」\"'”)]*)$", t, re.S | re.I)
        t = m.group(1).rstrip() + "</b>" + m.group(2)
    spans = BOLD_RE.findall(t)
    if len(spans) != 1 or not spans[0].strip():
        raise TranslateError(f"expected exactly one <b>...</b>, got {len(spans)}: {t}")
    if t.lower().count("<b>") != 1 or t.lower().count("</b>") != 1:
        raise TranslateError(f"unbalanced bold tags: {t}")
    if re.search(r"[぀-ヿ一-鿿]", spans[0]):
        raise TranslateError(f"the bold part is Japanese: {t}")
    return t


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise TranslateError(f"HTTP {e.code} from {url}: {detail}") from None
    except urllib.error.URLError as e:
        raise TranslateError(f"cannot reach {url}: {e.reason}") from None


def _parse_json(text):
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise TranslateError(f"no JSON in answer: {text[:200]}")
    try:
        return json.loads(m.group(0))["translation"]
    except (ValueError, KeyError) as e:
        raise TranslateError(f"bad JSON in answer: {text[:200]}") from e


def ask_claude(c, messages):
    # Raw HTTP: Anki's bundled Python cannot install the anthropic SDK (it needs compiled wheels).
    key = (c.get("CLAUDE_API_KEY") or "").strip()
    if not key:
        raise TranslateError("CLAUDE_API_KEY is empty. Add your key in Tools > Add-ons > Sentence Translate > Config.")
    model = c.get("CLAUDE_MODEL") or "claude-opus-5-5"
    body = {
        "model": model,
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": messages,
        "output_config": {
            "effort": c.get("CLAUDE_EFFORT") or "low",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2026-10-21",
    }
    if model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2"):
        body["fallbacks"] = "default"  # rerun on another model if a safety classifier declines
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    data = _post("https://api.anthropic.com/v1/messages", body, headers, c.get("TIMEOUT_SECONDS", 60))
    usage = data.get("usage") or {}
    for k in ("input_tokens", "output_tokens"):
        USAGE[k] = USAGE.get(k, 0) + int(usage.get(k) or 0)
    if data.get("stop_reason") == "refusal":
        raise TranslateError("Claude declined to translate this sentence.")
    if data.get("stop_reason") == "max_tokens":
        raise TranslateError("Claude's answer was cut off (max_tokens).")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return _parse_json(text)


def ask_ollama(c, messages):
    body = {
        "model": c.get("OLLAMA_MODEL") or "gemma4:12b",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "format": SCHEMA,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2},
    }
    url = (c.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/") + "/api/chat"
    data = _post(url, body, {"Content-Type": "application/json"}, c.get("TIMEOUT_SECONDS", 60))
    return _parse_json(data.get("message", {}).get("content", ""))


def ask_openai_compatible(c, messages):
    """LM Studio, llama.cpp server, vLLM and other servers with /v1/chat/completions."""
    body = {
        "model": c.get("OPENAI_COMPAT_MODEL") or "",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": 0.2,
        "response_format": {"type": "json_schema", "json_schema": {"name": "translation", "schema": SCHEMA}},
    }
    headers = {"Content-Type": "application/json"}
    if c.get("OPENAI_COMPAT_API_KEY"):
        headers["Authorization"] = "Bearer " + c["OPENAI_COMPAT_API_KEY"]
    url = (c.get("OPENAI_COMPAT_URL") or "http://127.0.0.1:1234/v1").rstrip("/") + "/chat/completions"
    data = _post(url, body, headers, c.get("TIMEOUT_SECONDS", 60))
    return _parse_json(data["choices"][0]["message"]["content"])


ENGINES = {"claude": ask_claude, "ollama": ask_ollama, "openai_compatible": ask_openai_compatible}


def translate(c, sentence, expression, reading="", meaning=""):
    """Translate one sentence; retry with feedback when the bold check fails."""
    engine = ENGINES.get(c.get("ENGINE", "claude"))
    if engine is None:
        raise TranslateError(f"unknown ENGINE {c.get('ENGINE')!r}; use one of {', '.join(ENGINES)}")
    messages = build_messages(sentence, expression, reading, meaning)
    last = None
    for _ in range(1 + int(c.get("RETRIES", 2))):
        answer = engine(c, messages)
        try:
            return check(answer)
        except TranslateError as e:
            last = e
            messages = messages + [
                {"role": "assistant", "content": json.dumps({"translation": answer}, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": f"Your translation must contain exactly one <b>...</b> around the English "
                    f"equivalent of {expression}. Problem: {e}. Answer again with JSON only.",
                },
            ]
    raise last

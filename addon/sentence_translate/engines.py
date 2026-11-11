"""Translation engines and the bold check. No Anki imports, so it can be tested outside Anki."""
import html
import json
import re
import time
import urllib.error
import urllib.request

SYSTEM_TEMPLATE = """You translate {src} example sentences from flashcards into {tgt} for a language learner.

Rules:
- Translate only the given sentence into natural, idiomatic {tgt}. Keep its tone (casual, rude, polite, childish) and keep it about as short as the original.
- If the sentence is a fragment, translate it as a fragment. Do not add explanations, notes, romanization or quotation marks around the whole sentence.
- The flashcard teaches one target word. In your {tgt} sentence, wrap the {tgt} word or short phrase that carries the meaning of the target word in this context in exactly one <b>...</b> pair. Use no other HTML.
- Bold the {tgt} equivalent, never the original word. If the target word has no single-word equivalent (a particle, sound effect or grammatical ending), bold the shortest phrase that expresses it.

Answer only with JSON: {{"translation": "..."}}"""

# Few-shot examples, used only for Japanese (or auto-detected) to English.
EXAMPLES = [
    ("竜は<b>左目</b>がつぶれてる。", "左目", "The dragon has its <b>left eye</b> crushed."),
    ("あ、黒部さん、こんなところで<b>奇遇</b>ですね。", "奇遇", "Ah, Mr. Kurobe, what a <b>coincidence</b> meeting you here."),
    ("なぜ掃除人に<b>変装</b>などを？", "変装", "Why are you <b>disguised</b> as a janitor?"),
]

CJK_LANGUAGES = ("japanese", "chinese", "korean", "cantonese", "mandarin")
LANGUAGES = [
    "Auto-detect", "Japanese", "Chinese", "Korean", "English", "Spanish", "French", "German", "Italian",
    "Portuguese", "Russian", "Swedish", "Norwegian", "Danish", "Finnish", "Dutch", "Polish", "Ukrainian",
    "Turkish", "Arabic", "Hindi", "Indonesian", "Vietnamese", "Thai",
]

SCHEMA = {
    "type": "object",
    "properties": {"translation": {"type": "string"}},
    "required": ["translation"],
    "additionalProperties": False,
}

BOLD_RE = re.compile(r"<b>(.+?)</b>", re.S | re.I)
CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")

USAGE = {}  # running token totals (input_tokens, output_tokens) for the cloud engines


class TranslateError(Exception):
    pass


def languages(c):
    src = (c.get("SOURCE_LANGUAGE") or "Auto-detect").strip()
    tgt = (c.get("TARGET_LANGUAGE") or "English").strip()
    return src, tgt


def system_prompt(c):
    src, tgt = languages(c)
    src_text = "foreign-language" if src.lower().startswith("auto") else src
    return SYSTEM_TEMPLATE.format(src=src_text, tgt=tgt)


def clean_sentence(raw):
    """Field HTML -> text, keeping <b> marks (the target word is usually bolded in Sentence)."""
    text = re.sub(r"<br\s*/?>|</div>\s*<div>", " ", raw or "", flags=re.I)
    text = re.sub(r"<(?!/?b>)[^>]*>", "", text, flags=re.I)
    text = re.sub(r"\[[^\]]*\]", "", text)  # furigana brackets, if any
    return html.unescape(text).replace("\xa0", " ").strip()


def plain(raw):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", raw or ""))).strip()


def user_message(sentence, expression, reading="", meaning=""):
    lines = [f"Sentence: {sentence}", f"Target word: {expression}"]
    if reading:
        lines.append(f"Reading: {reading}")
    if meaning:
        lines.append(f"Dictionary meaning (hint only): {meaning[:300]}")
    return "\n".join(lines)


def build_messages(c, sentence, expression, reading="", meaning=""):
    src, tgt = languages(c)
    msgs = []
    if tgt.lower() == "english" and (src.lower().startswith("auto") or src.lower() == "japanese"):
        for jp, word, en in EXAMPLES:
            msgs.append({"role": "user", "content": user_message(jp, word)})
            msgs.append({"role": "assistant", "content": json.dumps({"translation": en}, ensure_ascii=False)})
    msgs.append({"role": "user", "content": user_message(sentence, expression, reading, meaning)})
    return msgs


def check(translation, target_language="English"):
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
    if target_language.lower() not in CJK_LANGUAGES and CJK_RE.search(spans[0]):
        raise TranslateError(f"the bold part is not in {target_language}: {t}")
    return t


def _post(url, body, headers, timeout, retries=3):
    """POST JSON; wait and try again on rate limits (429) and overload (503, 529)."""
    data = json.dumps(body).encode("utf-8")
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                wait = float(e.headers.get("retry-after") or 0)
            except ValueError:
                wait = 0
            m = re.search(r'"retryDelay":\s*"(\d+)', detail)  # Gemini puts it in the body
            if m:
                wait = max(wait, float(m.group(1)))
            if e.code == 429 and wait > 120:
                raise TranslateError(
                    "The API's quota is used up for now (for Gemini's free tier this is often a daily limit "
                    f"per model; try again in about {wait / 3600:.1f} hours or choose another model). {detail[:300]}"
                ) from None
            if e.code in (429, 503, 529) and attempt < retries:
                time.sleep(min(max(wait, 5 * (attempt + 1)), 60))
                continue
            raise TranslateError(f"HTTP {e.code} from {url.split('?')[0]}: {detail[:500]}") from None
        except urllib.error.URLError as e:
            raise TranslateError(f"cannot reach {url.split('?')[0]}: {e.reason}") from None
        except TimeoutError:
            if attempt < 1:
                continue
            raise TranslateError(f"no answer from {url.split('?')[0]} within {timeout} seconds") from None


def _parse_json(text):
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise TranslateError(f"no JSON in answer: {text[:200]}")
    try:
        return json.loads(m.group(0))["translation"]
    except (ValueError, KeyError) as e:
        raise TranslateError(f"bad JSON in answer: {text[:200]}") from e


def _count(inp, out):
    USAGE["input_tokens"] = USAGE.get("input_tokens", 0) + int(inp or 0)
    USAGE["output_tokens"] = USAGE.get("output_tokens", 0) + int(out or 0)


def ask_claude(c, messages):
    # Raw HTTP: Anki's bundled Python cannot install the anthropic SDK (it needs compiled wheels).
    key = (c.get("CLAUDE_API_KEY") or "").strip()
    if not key:
        raise TranslateError("The Claude API key is empty. Add it in Tools > Add-ons > Sentence Translate > Config.")
    model = c.get("CLAUDE_MODEL") or "claude-opus-5-5"
    body = {
        "model": model,
        "max_tokens": 4000,
        "system": system_prompt(c),
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
    _count(usage.get("input_tokens"), usage.get("output_tokens"))
    if data.get("stop_reason") == "refusal":
        raise TranslateError("Claude declined to translate this sentence.")
    if data.get("stop_reason") == "max_tokens":
        raise TranslateError("Claude's answer was cut off (max_tokens).")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return _parse_json(text)


def ask_gemini(c, messages):
    key = (c.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise TranslateError("The Gemini API key is empty. Get one at aistudio.google.com/apikey and add it in the add-on config.")
    model = c.get("GEMINI_MODEL") or "gemini-3.6-flash"
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt(c)}]},
        "contents": [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {"translation": {"type": "string"}},
                "required": ["translation"],
            },
        },
    }
    if c.get("GEMINI_THINKING") and model.startswith("gemini-3"):
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": c["GEMINI_THINKING"]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    data = _post(url, body, headers, c.get("TIMEOUT_SECONDS", 60))
    usage = data.get("usageMetadata") or {}
    _count(usage.get("promptTokenCount"), (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0))
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no answer")
        raise TranslateError(f"Gemini returned no translation ({reason}).")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise TranslateError(f"Gemini returned no text (finishReason {cands[0].get('finishReason')}).")
    return _parse_json(text)


def ask_ollama(c, messages):
    body = {
        "model": c.get("OLLAMA_MODEL") or "gemma4:12b",
        "messages": [{"role": "system", "content": system_prompt(c)}] + messages,
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
        "messages": [{"role": "system", "content": system_prompt(c)}] + messages,
        "temperature": 0.2,
        "response_format": {"type": "json_schema", "json_schema": {"name": "translation", "schema": SCHEMA}},
    }
    headers = {"Content-Type": "application/json"}
    if c.get("OPENAI_COMPAT_API_KEY"):
        headers["Authorization"] = "Bearer " + c["OPENAI_COMPAT_API_KEY"]
    url = (c.get("OPENAI_COMPAT_URL") or "http://127.0.0.1:1234/v1").rstrip("/") + "/chat/completions"
    data = _post(url, body, headers, c.get("TIMEOUT_SECONDS", 60))
    return _parse_json(data["choices"][0]["message"]["content"])


ENGINES = {
    "claude": ask_claude,
    "gemini": ask_gemini,
    "ollama": ask_ollama,
    "openai_compatible": ask_openai_compatible,
}


def translate(c, sentence, expression, reading="", meaning=""):
    """Translate one sentence; retry with feedback when the bold check fails."""
    engine = ENGINES.get(c.get("ENGINE", "claude"))
    if engine is None:
        raise TranslateError(f"unknown ENGINE {c.get('ENGINE')!r}; use one of {', '.join(ENGINES)}")
    _, tgt = languages(c)
    messages = build_messages(c, sentence, expression, reading, meaning)
    last = None
    for _ in range(1 + int(c.get("RETRIES", 2))):
        answer = engine(c, messages)
        try:
            return check(answer, tgt)
        except TranslateError as e:
            last = e
            messages = messages + [
                {"role": "assistant", "content": json.dumps({"translation": answer}, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": f"Your translation must contain exactly one <b>...</b> around the {tgt} "
                    f"equivalent of {expression}. Problem: {e}. Answer again with JSON only.",
                },
            ]
    raise last

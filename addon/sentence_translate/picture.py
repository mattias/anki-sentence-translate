"""Picture prompts (from a local or cloud text model) and image generation (local picture server).

No Anki imports, so it can be tested outside Anki.
"""
import base64
import json
import re

from . import engines

PROMPT_SYSTEM = """You write prompts for an image generator that makes small flashcard pictures for a learner of Japanese.

The flashcard teaches one Japanese target word, seen in an example sentence. The picture must show what the target word means in this sentence, so that the learner recalls the word's meaning by looking at it. It must not illustrate the rest of the sentence or the story around it.

Rules:
- Decide first whether the meaning can be shown in a picture. Concrete things (food, objects, animals, places, weather), visible actions, body states, emotions and character traits usually can. Function words, adverbs of degree or probability, conjunctions, discourse fillers and very abstract ideas usually cannot (for example 一応, だって, あながち, そのうち, とりあえず, 要するに).
- If it can, write one short English scene description (8 to 25 words): one clear subject doing or being the target meaning, with a simple setting. Show the meaning, not the word: for 腰抜け show a cowardly person trembling and hiding, not a broken back.
- For an action, freeze its most typical moment and spell out the body position and what touches what, so it cannot be mistaken for standing still (for 飛びつく: "a dog in mid-leap, front paws stretched out, about to land on a man's chest").
- Prefer the sentence's own situation, but if it would make an unclear or disturbing picture, use the clearest everyday example of the same meaning instead (for 吊るす with a rope: "a paper lantern hanging from a hook under a roof", never a hanging rope).
- Use generic people ("a young woman", "an old man", "a boy"), never names of characters, real people, brands or franchises from the sentence.
- No text, letters, numbers, speech bubbles or signs in the picture. Do not describe an art style; the style is added later.
- Nothing gory, sexual or hateful. For violent or dark words, show a mild, symbolic scene.

Answer only with JSON: {"depictable": true or false, "prompt": "..."} (prompt is "" when depictable is false)."""

PROMPT_SCHEMA = {
    "type": "object",
    "properties": {"depictable": {"type": "boolean"}, "prompt": {"type": "string"}},
    "required": ["depictable", "prompt"],
    "additionalProperties": False,
}

EXAMPLES = [
    ("今日は<b>雷</b>がすごいね。", "雷", "It's really <b>thundering</b> today.", True,
     "a bright lightning bolt striking from dark storm clouds over a small house at night"),
    ("<b>一応</b>聞いておくけど", "一応", "I'll ask <b>just in case</b>, but", False, ""),
    ("彼は<b>照れ</b>くさそうに笑った。", "照れる", "He laughed <b>bashfully</b>.", True,
     "a young man blushing and scratching the back of his head with an embarrassed smile"),
]

SCENE_SYSTEM = """You write prompts for an image generator that makes small flashcard pictures for a learner of Japanese.

The flashcard teaches a Japanese word that cannot be pictured by itself (a function word, an adverb, a filler or an abstract idea). Instead, the picture shows the situation of the example sentence, so that the learner remembers the moment in which the word is said.

Rules:
- Write one short English scene description (10 to 30 words) of the situation in the sentence: who is there, what they are doing, the mood, a simple setting. If someone speaks, show only the speaker's facial expression and gesture: never write what is said, and avoid the words "say", "talk", "tell", "promise", "ask", "explain" and "as if", because the image model then draws speech bubbles with text.
- Do not try to show the target word itself, and do not add things the sentence does not suggest.
- Use generic people ("a young woman", "an old man", "a boy"), never names of characters, real people, brands or franchises from the sentence.
- No text, letters, numbers, speech bubbles or signs in the picture. Do not describe an art style; the style is added later.
- Nothing gory, sexual or hateful. For violent or dark situations, show a mild, symbolic scene.

Answer only with JSON: {"prompt": "..."}"""

SCENE_SCHEMA = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}},
    "required": ["prompt"],
    "additionalProperties": False,
}

SCENE_EXAMPLES = [
    ("<b>一応</b>聞いておくけど、明日は来るよね？", "一応", "I'll ask <b>just in case</b>, but you're coming tomorrow, right?",
     "a young woman leaning toward her friend at a cafe table with a slightly doubtful, raised-eyebrow look"),
]

STYLE = ("simple flat vector illustration, clean thick outlines, soft pastel colors, plain white background, "
         "centered, no text")


class PictureError(Exception):
    pass


def prompt_message(sentence, expression, translation="", meaning=""):
    lines = [f"Sentence: {sentence}", f"Target word: {expression}"]
    if translation:
        lines.append(f"Translation: {engines.plain(translation)}")
    if meaning:
        lines.append(f"Dictionary meaning: {meaning[:300]}")
    return "\n".join(lines)


def build_prompt_messages(sentence, expression, translation="", meaning=""):
    msgs = []
    for jp, word, en, ok, prompt in EXAMPLES:
        msgs.append({"role": "user", "content": prompt_message(jp, word, en)})
        msgs.append({"role": "assistant", "content": json.dumps({"depictable": ok, "prompt": prompt})})
    msgs.append({"role": "user", "content": prompt_message(sentence, expression, translation, meaning)})
    return msgs


def _parse(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise PictureError(f"no JSON in answer: {(text or '')[:200]}")
    try:
        d = json.loads(m.group(0))
    except ValueError as e:
        raise PictureError(f"bad JSON in answer: {text[:200]}") from e
    prompt = (d.get("prompt") or "").strip()
    if not d.get("depictable") or not prompt:
        return None
    return prompt


def ask_ollama(c, messages, system=PROMPT_SYSTEM, schema=PROMPT_SCHEMA):
    body = {
        "model": c.get("PICTURE_PROMPT_MODEL") or c.get("OLLAMA_MODEL") or "gemma4:12b",
        "messages": [{"role": "system", "content": system}] + messages,
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": c.get("PICTURE_PROMPT_KEEP_ALIVE", "2m"),
        "options": {"temperature": 0.3},
    }
    url = (c.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/") + "/api/chat"
    data = engines._post(url, body, {"Content-Type": "application/json"}, c.get("TIMEOUT_SECONDS", 90))
    return data.get("message", {}).get("content", "")


def unload_prompt_model(c):
    """Free the text model's GPU memory before the image model runs."""
    body = {"model": c.get("PICTURE_PROMPT_MODEL") or c.get("OLLAMA_MODEL") or "gemma4:12b", "keep_alive": 0}
    url = (c.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/") + "/api/generate"
    try:
        engines._post(url, body, {"Content-Type": "application/json"}, 30, retries=0)
    except Exception:
        pass


def server_url(c):
    return (c.get("PICTURE_SERVER_URL") or "http://127.0.0.1:8189").rstrip("/")


def scene_prompt(c, sentence, expression, translation="", meaning=""):
    """English description of the sentence's situation (for words that cannot be pictured themselves)."""
    msgs = []
    for jp, word, en, prompt in SCENE_EXAMPLES:
        msgs.append({"role": "user", "content": prompt_message(jp, word, en)})
        msgs.append({"role": "assistant", "content": json.dumps({"prompt": prompt})})
    msgs.append({"role": "user", "content": prompt_message(sentence, expression, translation, meaning)})
    text = ask_ollama(c, msgs, SCENE_SYSTEM, SCENE_SCHEMA)
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        prompt = (json.loads(m.group(0)).get("prompt") or "").strip() if m else ""
    except ValueError:
        prompt = ""
    if not prompt:
        raise PictureError(f"no scene description in answer: {(text or '')[:200]}")
    return prompt


def picture_prompt(c, sentence, expression, translation="", meaning=""):
    """(prompt, kind): kind "word" for a picture of the word, "scene" for the sentence's situation when the word
    cannot be pictured and PICTURE_UNDEPICTABLE is "scene"; (None, None) when the note is left as it is."""
    p = _parse(ask_ollama(c, build_prompt_messages(sentence, expression, translation, meaning)))
    if p:
        return p, "word"
    if (c.get("PICTURE_UNDEPICTABLE") or "scene") == "scene":
        return scene_prompt(c, sentence, expression, translation, meaning), "scene"
    return None, None


def generate(c, prompt):
    """Ask the local picture server for an image; returns (bytes, extension)."""
    url = server_url(c) + "/generate"
    body = {
        "prompt": f"{prompt}, {c.get('PICTURE_STYLE') or STYLE}",
        "size": int(c.get("PICTURE_SIZE") or 512),
        "steps": int(c.get("PICTURE_STEPS") or 4),
        "format": "webp",
    }
    data = engines._post(url, body, {"Content-Type": "application/json"}, c.get("PICTURE_TIMEOUT_SECONDS", 180))
    if "image" not in data:
        raise PictureError(f"the picture server returned no image: {str(data)[:200]}")
    return base64.b64decode(data["image"]), data.get("ext", "webp")

# Sentence Translate for Anki

Translates the example sentence on your cards (`Sentence`) into your language and writes it to `SentenceTranslation`, with the translation of the target word (`Expression`) in **bold**, for example:

> 竜は**左目**がつぶれてる。 → The dragon has its **left eye** crushed.

Made for mining cards from Yomitan (Kiku and similar note types). Source and target language and all field names are configurable.

## Install

Download `sentence_translate.ankiaddon` from the [releases page](https://github.com/mattias/anki-sentence-translate/releases) and open it with Anki, then restart Anki. Open *Tools > Sentence Translate settings...* (or *Config* in the add-ons list), choose an engine, paste its API key, pick the languages and click **Test connection**.

## Use

- **New notes:** with *Translate new notes automatically* on (default), notes added from Yomitan (via AnkiConnect) or the Add window are translated in the background. The note is created immediately; if the translation fails, the note simply has no translation.
- **Card browser:** select notes, then *Cards > Translate Sentence (English)* (`Ctrl+Alt+T`; the menu shows your target language). Runs in the background with progress and cancel. *Edit > Undo* reverts the whole run.
- **Editor:** the **EN** button (`Ctrl+Shift+T`; it shows the code of your target language) translates the current note.

An existing translation is never replaced without asking. Every answer is checked: it must contain exactly one `<b>...</b>`, and for a non-CJK target language the bold part must not be CJK text; otherwise the model is asked again (`RETRIES`), and a note that still fails is left unchanged.

## Engines

| Engine | What | Notes |
|---|---|---|
| Claude API (default) | `claude-opus-5-5` | Best with names, nuance and long sentences. Key from console.anthropic.com. |
| Google Gemini API | `gemini-3.6-flash` | Free tier with a daily request limit per model. Key from aistudio.google.com/apikey. |
| Ollama | e.g. `gemma4:12b` | Local, free and offline. More mistakes with names, long sentences and which word to bold. |
| OpenAI-compatible | LM Studio, llama.cpp server, etc. | Any `/v1/chat/completions` server. |

Measured on 14 real Japanese mining sentences into English, cost per 1000 notes:

| Model | Cost per 1000 notes | Quality |
|---|---|---|
| `claude-opus-5-5` | about $3.90 | Best: names, wordplay and context-dependent words |
| `claude-sonnet-5-5` | about $2.10 | Very close to Opus |
| `gemini-3.6-flash` (thinking `low`) | free within the free tier, about $0.37 paid | Close to Sonnet |
| `gemini-3.5-flash-lite` | free within the free tier, about $0.20 paid | Good for plain sentences, misses more nuance |
| `claude-haiku-5-5` | about $0.11 | Good for plain sentences; bolds the wrong word more often |

Gemini's free tier limits requests per model per day, and the limits differ between models (in November 2026 `gemini-3.8-flash` allowed only 20 requests a day on a new project). When the daily quota is used up, the add-on says so instead of waiting. The Claude API is billed separately from a Claude.ai subscription; you can set a monthly spend limit in the Anthropic Console.

API keys are kept in Anki's add-on config on your computer. The sentence, target word, reading and dictionary meaning are sent to the engine you choose.

## Build

```
python build.py
```

creates `sentence_translate.ankiaddon`.

# Sentence Translate for Anki

Translates the Japanese `Sentence` field to English and writes it to `SentenceTranslation`, with the English equivalent of the target word (`Expression`) in **bold**, for example:

> 竜は**左目**がつぶれてる。 → The dragon has its **left eye** crushed.

Made for the Kiku note type; field names can be changed in the config.

## Install

Download `sentence_translate.ankiaddon` from the [releases page](https://github.com/mattias/anki-sentence-translate/releases) and open it with Anki, then restart Anki. Set `ENGINE` and, for Claude, `CLAUDE_API_KEY` in *Tools > Add-ons > Sentence Translate > Config*, and run *Tools > Sentence Translate: test engine*.

## Use

- Card browser: select notes, then *Cards > Translate Sentence to English* (`Ctrl+Alt+T`). Runs in the background with progress and cancel. *Edit > Undo* reverts the whole run.
- Editor: the **EN** button (`Ctrl+Shift+T`) translates the current note.
- *Tools > Sentence Translate: test engine* checks the settings with one sentence.

An existing translation is never replaced without asking. Every answer is checked: it must contain exactly one `<b>...</b>` around English text; otherwise the model is asked again (`RETRIES`), and a note that still fails is left unchanged.

## Engines

Choose with `ENGINE` in *Tools > Add-ons > Sentence Translate > Config*:

| `ENGINE` | What | Notes |
|---|---|---|
| `claude` (default) | Claude API, `claude-opus-5-5` | Best with names, nuance and long sentences. Needs `CLAUDE_API_KEY` from console.anthropic.com. |
| `ollama` | Local Ollama model, e.g. `gemma4:12b` | Free and offline. Fine for short sentences; more mistakes with names, long sentences and which word to bold. |
| `openai_compatible` | LM Studio, llama.cpp server, etc. | Any `/v1/chat/completions` server. |

Measured cost with the Claude API (14 real sentences, `CLAUDE_EFFORT` `low`), per 1000 notes:

| `CLAUDE_MODEL` | Cost per 1000 notes | Quality |
|---|---|---|
| `claude-opus-5-5` | about $3.90 | Best: names, wordplay and context-dependent words |
| `claude-sonnet-5-5` | about $2.10 | Very close to Opus |
| `claude-haiku-5-5` | about $0.11 | Good for plain sentences; bolds the wrong word more often |

The API is billed separately from a Claude.ai subscription. You can set a monthly spend limit in the Anthropic Console.

The API key is kept in Anki's add-on config on your computer. The sentence, target word, reading and dictionary meaning are sent to the engine you choose.

## Build

```
python build.py
```

creates `sentence_translate.ankiaddon`.

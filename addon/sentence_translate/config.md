**Sentence Translate** writes an English translation of `Sentence` into `SentenceTranslation`, with the English equivalent of `Expression` in **bold**.

Use it from the card browser (*Cards > Translate Sentence to English*, default `Ctrl+Alt+T`) or the **EN** button in the editor (default `Ctrl+Shift+T`). *Tools > Sentence Translate: test engine* checks your settings with one sentence.

- `ENGINE`: `claude` (best quality, needs an API key and costs a little per sentence), `ollama` (local, free) or `openai_compatible` (LM Studio, llama.cpp server and similar).
- `CLAUDE_API_KEY`: your key from console.anthropic.com. It is stored in Anki's add-on config on this computer only.
- `CLAUDE_MODEL`: default `claude-opus-5-5`. `claude-sonnet-5-5` and `claude-haiku-5-5` are cheaper. `CLAUDE_EFFORT`: `low` (default), `medium` or `high`.
- `OLLAMA_URL` / `OLLAMA_MODEL`: local Ollama server and model name as shown by `ollama list`.
- `OPENAI_COMPAT_URL` / `OPENAI_COMPAT_MODEL` / `OPENAI_COMPAT_API_KEY`: any server with `/v1/chat/completions`.
- `SOURCE_FIELD`, `TARGET_FIELD`, `EXPRESSION_FIELD`: field names. `READING_FIELD`, `MEANING_FIELD`: optional hints for the model (empty string = not sent).
- `TAG`: tag added to translated notes. Empty string = no tag.
- `RETRIES`: how often to ask again when the answer does not have exactly one bold word.
- `EDITOR_SHORTCUT`, `BROWSER_SHORTCUT`: keyboard shortcuts (restart Anki after changing). Empty string = none.

Existing translations are never replaced without asking. A browser run can be undone with *Edit > Undo*.

Sentence Translate has its own settings window: click **Config** (or *Tools > Sentence Translate settings...*). This text is shown only if that window cannot open.

- `ENGINE`: `claude`, `gemini`, `ollama` or `openai_compatible`.
- `SOURCE_LANGUAGE` (`Auto-detect` or a language name) and `TARGET_LANGUAGE` (for example `English`).
- `AUTO_TRANSLATE_NEW`: translate notes in the background when they are added, for example from Yomitan.
- `CLAUDE_API_KEY`, `CLAUDE_MODEL`, `CLAUDE_EFFORT`; `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_THINKING`; `OLLAMA_URL`, `OLLAMA_MODEL`; `OPENAI_COMPAT_URL`, `OPENAI_COMPAT_MODEL`, `OPENAI_COMPAT_API_KEY`.
- Field names: `SOURCE_FIELD`, `TARGET_FIELD`, `EXPRESSION_FIELD`, and the optional hints `READING_FIELD`, `MEANING_FIELD`.
- `TAG`, `RETRIES`, `TIMEOUT_SECONDS`, `EDITOR_SHORTCUT`, `BROWSER_SHORTCUT`.
- Pictures (*Browse > Cards > Add picture of the word*): `PICTURE_FIELD`, `PICTURE_PROMPT_MODEL` (an Ollama model that writes the scene description), `PICTURE_SERVER_URL` and `PICTURE_INSTALL_DIR` (the local FLUX.1 [schnell] server, set up from *Tools > Sentence Translate: set up pictures*), `PICTURE_EMPTY_IF_FILE_CONTAINS` (for example `audiobook_cover_`: such images count as no picture), `PICTURE_IDLE_MINUTES`, `PICTURE_STYLE`, `PICTURE_SIZE`, `PICTURE_STEPS`, `PICTURE_TAG`, `PICTURE_SHORTCUT`.

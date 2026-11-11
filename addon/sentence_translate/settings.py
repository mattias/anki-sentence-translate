"""Settings window (replaces the raw JSON config editor)."""
from aqt import mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import showInfo, showWarning, tooltip

from . import engines

ENGINE_LABELS = [
    ("claude", "Claude API (best quality, paid)"),
    ("gemini", "Google Gemini API (free tier available)"),
    ("ollama", "Ollama (local, free)"),
    ("openai_compatible", "OpenAI-compatible server (LM Studio and others)"),
]
CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
GEMINI_MODELS = ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]

# (config key, label, kind, extra) ; kind: text, secret, combo (editable), choice, check, int
SECTIONS = [
    ("Translation", [
        ("SOURCE_LANGUAGE", "Translate from", "combo", engines.LANGUAGES),
        ("TARGET_LANGUAGE", "Translate into", "combo", engines.LANGUAGES[1:]),
        ("AUTO_TRANSLATE_NEW", "Translate new notes automatically when they are added (Yomitan, Add window)", "check", None),
    ]),
    ("Claude", [
        ("CLAUDE_API_KEY", "API key (console.anthropic.com)", "secret", None),
        ("CLAUDE_MODEL", "Model", "combo", CLAUDE_MODELS),
        ("CLAUDE_EFFORT", "Effort", "choice", ["low", "medium", "high"]),
    ]),
    ("Gemini", [
        ("GEMINI_API_KEY", "API key (aistudio.google.com/apikey)", "secret", None),
        ("GEMINI_MODEL", "Model", "combo", GEMINI_MODELS),
        ("GEMINI_THINKING", "Thinking (Gemini 3 models)", "choice", ["low", "medium", "high"]),
    ]),
    ("Ollama", [
        ("OLLAMA_URL", "Server", "text", None),
        ("OLLAMA_MODEL", "Model", "text", None),
    ]),
    ("OpenAI-compatible server", [
        ("OPENAI_COMPAT_URL", "Server (…/v1)", "text", None),
        ("OPENAI_COMPAT_MODEL", "Model", "text", None),
        ("OPENAI_COMPAT_API_KEY", "API key (if needed)", "secret", None),
    ]),
    ("Pictures (Browse > Cards > Add picture of the word)", [
        ("PICTURE_FIELD", "Picture field", "text", None),
        ("PICTURE_PROMPT_MODEL", "Ollama model that describes the picture", "text", None),
        ("PICTURE_SERVER_URL", "Picture server", "text", None),
        ("PICTURE_INSTALL_DIR", "Install folder (empty: the default folder)", "text", None),
        ("PICTURE_EMPTY_IF_FILE_CONTAINS", "Count images whose file name contains this as no picture (e.g. book covers)", "text", None),
        ("PICTURE_IDLE_MINUTES", "Free the GPU after this many idle minutes", "int", (1, 120)),
        ("PICTURE_STYLE", "Style added to every picture", "text", None),
        ("PICTURE_SIZE", "Size in pixels", "int", (256, 1024)),
        ("PICTURE_STEPS", "Steps (FLUX schnell: 4)", "int", (1, 8)),
        ("PICTURE_TAG", "Tag for notes with a new picture", "text", None),
        ("PICTURE_SHORTCUT", "Browser shortcut (restart Anki)", "text", None),
    ]),
    ("Fields and options", [
        ("SOURCE_FIELD", "Sentence field", "text", None),
        ("TARGET_FIELD", "Translation field", "text", None),
        ("EXPRESSION_FIELD", "Target word field", "text", None),
        ("READING_FIELD", "Reading field (optional hint)", "text", None),
        ("MEANING_FIELD", "Meaning field (optional hint)", "text", None),
        ("TAG", "Tag for translated notes", "text", None),
        ("RETRIES", "Extra tries when the bold word is wrong", "int", (0, 5)),
        ("EDITOR_SHORTCUT", "Editor shortcut (restart Anki)", "text", None),
        ("BROWSER_SHORTCUT", "Browser shortcut (restart Anki)", "text", None),
    ]),
]


class SettingsDialog(QDialog):
    def __init__(self, addon_name, parent=None):
        super().__init__(parent or mw)
        self.addon_name = addon_name
        defaults = mw.addonManager.addonConfigDefaults(addon_name) or {}
        self.conf = {**defaults, **(mw.addonManager.getConfig(addon_name) or {})}
        self.setWindowTitle("Sentence Translate settings")
        self.resize(620, 720)
        self.widgets = {}

        outer = QVBoxLayout(self)
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("<b>Engine</b>"))
        self.engine = QComboBox()
        for key, label in ENGINE_LABELS:
            self.engine.addItem(label, key)
        idx = max(0, self.engine.findData(self.conf.get("ENGINE", "claude")))
        self.engine.setCurrentIndex(idx)
        engine_row.addWidget(self.engine, 1)
        test = QPushButton("Test connection")
        test.clicked.connect(self.test)
        engine_row.addWidget(test)
        outer.addLayout(engine_row)

        body = QWidget()
        col = QVBoxLayout(body)
        for title, rows in SECTIONS:
            box = QGroupBox(title)
            form = QFormLayout(box)
            for key, label, kind, extra in rows:
                w = self.make(kind, self.conf.get(key), extra)
                self.widgets[key] = (kind, w)
                if kind == "check":
                    w.setText(label)
                    form.addRow(w)
                else:
                    form.addRow(label, w)
            col.addWidget(box)
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        note = QLabel(
            "API keys are stored in Anki's add-on config on this computer. The sentence, target word, "
            "reading and meaning are sent to the engine you choose."
        )
        note.setWordWrap(True)
        outer.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    @staticmethod
    def make(kind, value, extra):
        if kind == "check":
            w = QCheckBox()
            w.setChecked(bool(value))
        elif kind == "int":
            w = QSpinBox()
            w.setRange(*extra)
            w.setValue(int(value or 0))
        elif kind in ("combo", "choice"):
            w = QComboBox()
            w.setEditable(kind == "combo")
            w.addItems(extra)
            if value and w.findText(str(value)) < 0:
                w.addItem(str(value))
            if value:
                w.setCurrentText(str(value))
        else:
            w = QLineEdit(str(value or ""))
            if kind == "secret":
                w.setEchoMode(QLineEdit.EchoMode.Password)
        return w

    def values(self):
        c = dict(self.conf)
        c["ENGINE"] = self.engine.currentData()
        for key, (kind, w) in self.widgets.items():
            if kind == "check":
                c[key] = w.isChecked()
            elif kind == "int":
                c[key] = w.value()
            elif kind in ("combo", "choice"):
                c[key] = w.currentText().strip()
            else:
                c[key] = w.text().strip()
        return c

    def test(self):
        c = self.values()
        src, _ = engines.languages(c)
        sample = ("あの店、<b>行列</b>ができてるね。", "行列") if src.lower() in ("auto-detect", "japanese") else None
        if sample is None:
            showInfo("Test connection uses a Japanese sentence. Set 'Translate from' to Auto-detect or Japanese to test.", parent=self)
            return

        def done(fut):
            try:
                showInfo(f"It works:\n\n{engines.plain(sample[0])}\n→ {fut.result()}", parent=self)
            except Exception as e:
                showWarning(f"The test failed:\n\n{e}", parent=self)

        tooltip("Testing...", parent=self)
        mw.taskman.run_in_background(lambda: engines.translate(c, *sample), done)

    def save(self):
        mw.addonManager.writeConfig(self.addon_name, self.values())
        self.accept()


def open_settings(addon_name):
    SettingsDialog(addon_name).exec()

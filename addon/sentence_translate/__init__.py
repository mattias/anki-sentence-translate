"""Sentence Translate: translate the Sentence field to English with the target word in bold."""
from aqt import gui_hooks, mw
from aqt.operations import CollectionOp, QueryOp
from aqt.operations.note import update_note
from aqt.qt import QAction, QMessageBox
from aqt.utils import showInfo, showWarning, tooltip

from . import engines

ADDON_NAME = "Sentence Translate"


def cfg():
    return mw.addonManager.getConfig(__name__)


def engine_label(c):
    e = c.get("ENGINE", "claude")
    model = {"claude": c.get("CLAUDE_MODEL"), "ollama": c.get("OLLAMA_MODEL"),
             "openai_compatible": c.get("OPENAI_COMPAT_MODEL")}.get(e, "")
    return f"{e} ({model})" if model else e


def note_input(c, note):
    """(sentence, expression, reading, meaning) from a note, or None if the note lacks the fields or a sentence."""
    for f in (c["SOURCE_FIELD"], c["TARGET_FIELD"], c["EXPRESSION_FIELD"]):
        if f not in note:
            return None
    sentence = engines.clean_sentence(note[c["SOURCE_FIELD"]])
    if not sentence:
        return None
    get = lambda name: engines.plain(note[name]) if name and name in note else ""
    return sentence, get(c["EXPRESSION_FIELD"]), get(c.get("READING_FIELD")), get(c.get("MEANING_FIELD"))


def translate_one(c, inp):
    return engines.translate(c, *inp)


def ask_overwrite(parent, count, total):
    box = QMessageBox(parent)
    box.setWindowTitle(ADDON_NAME)
    box.setText(f"{count} of {total} notes already have a {cfg()['TARGET_FIELD']}.\n\nReplace those translations?")
    replace = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
    skip = box.addButton("Skip them", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(skip)
    box.exec()
    if box.clickedButton() is replace:
        return "replace"
    if box.clickedButton() is skip:
        return "skip"
    return None


# ---- Browser: selected notes ----

def run_browser(browser):
    c = cfg()
    nids = list(browser.selected_notes())
    if not nids:
        tooltip("No notes selected.")
        return
    todo, existing, skipped = [], [], 0
    for nid in nids:
        note = mw.col.get_note(nid)
        inp = note_input(c, note)
        if inp is None:
            skipped += 1
            continue
        (existing if engines.plain(note[c["TARGET_FIELD"]]) else todo).append((nid, inp))
    if existing:
        choice = ask_overwrite(browser, len(existing), len(todo) + len(existing))
        if choice is None:
            return
        if choice == "replace":
            todo += existing
    if not todo:
        showInfo(f"Nothing to translate. Skipped {len(existing)} with a translation and {skipped} without the fields or a sentence.")
        return

    def work(_col):
        done, failed = [], []
        total = len(todo)
        for i, (nid, inp) in enumerate(todo):
            if mw.progress.want_cancel():
                break
            mw.taskman.run_on_main(
                lambda i=i: mw.progress.update(label=f"Translating {i + 1} of {total}", value=i, max=total)
            )
            try:
                done.append((nid, translate_one(c, inp)))
            except Exception as e:  # one bad note must not stop the run
                print(f"{ADDON_NAME}: note {nid}: {e}")
                failed.append((nid, str(e)))
                if not done and len(failed) >= 3:
                    break  # probably a config problem (key, server); stop early
        return done, failed

    def on_done(result):
        done, failed = result
        if not done:
            msg = failed[0][1] if failed else "Cancelled."
            showWarning(f"No notes were translated.\n\n{msg}")
            return

        def write(col):
            notes = []
            for nid, text in done:
                note = col.get_note(nid)
                note[c["TARGET_FIELD"]] = text
                if c.get("TAG"):
                    note.add_tag(c["TAG"])
                notes.append(note)
            return col.update_notes(notes)

        def finished(_changes):
            msg = f"Translated {len(done)} notes with {engine_label(c)}."
            if failed:
                msg += f"\n{len(failed)} failed, for example:\n{failed[0][1][:300]}"
            showInfo(msg + "\n\nEdit > Undo reverts the whole run.")

        CollectionOp(browser, write).success(finished).run_in_background()

    QueryOp(parent=browser, op=work, success=on_done).with_progress(
        f"Translating with {engine_label(c)}..."
    ).run_in_background()


def setup_browser_menu(browser):
    action = QAction("Translate Sentence to English", browser)
    keys = cfg().get("BROWSER_SHORTCUT")
    if keys:
        action.setShortcut(keys)
    action.triggered.connect(lambda: run_browser(browser))
    browser.form.menu_Cards.addSeparator()
    browser.form.menu_Cards.addAction(action)


# ---- Editor: the current note ----

def run_editor(editor):
    c = cfg()
    note = editor.note
    if note is None:
        return
    inp = note_input(c, note)
    if inp is None:
        tooltip(f"This note needs {c['SOURCE_FIELD']}, {c['EXPRESSION_FIELD']} and {c['TARGET_FIELD']}, and a sentence.")
        return
    if engines.plain(note[c["TARGET_FIELD"]]):
        box = QMessageBox.question(
            editor.widget, ADDON_NAME,
            f"{c['TARGET_FIELD']} already has:\n\n{engines.plain(note[c['TARGET_FIELD']])}\n\nReplace it?",
        )
        if box != QMessageBox.StandardButton.Yes:
            return

    def done(fut):
        try:
            text = fut.result()
        except Exception as e:
            showWarning(f"Translation failed:\n\n{e}", parent=editor.widget)
            return
        if editor.note is not note:
            return  # the user moved to another note meanwhile
        note[c["TARGET_FIELD"]] = text
        if c.get("TAG"):
            note.add_tag(c["TAG"])
        if editor.addMode:
            editor.loadNoteKeepingFocus()
        else:
            update_note(parent=editor.widget, note=note).success(
                lambda _: editor.loadNoteKeepingFocus()
            ).run_in_background()
        tooltip("Translated.", parent=editor.widget)

    tooltip(f"Translating with {engine_label(c)}...", parent=editor.widget)
    mw.taskman.run_in_background(lambda: translate_one(c, inp), done)


def on_editor_saved(editor):
    editor.call_after_note_saved(lambda: run_editor(editor))


def setup_editor_button(buttons, editor):
    keys = cfg().get("EDITOR_SHORTCUT") or None
    tip = "Translate Sentence to English" + (f" ({keys})" if keys else "")
    buttons.append(editor.addButton(None, "sentence_translate", on_editor_saved, tip=tip, label="EN", keys=keys))


# ---- Tools menu: test the engine ----

def test_engine():
    c = cfg()
    sample = ("あの店、<b>行列</b>ができてるね。", "行列")

    def done(fut):
        try:
            showInfo(f"{engine_label(c)} works:\n\n{engines.plain(sample[0])}\n→ {fut.result()}")
        except Exception as e:
            showWarning(f"{engine_label(c)} failed:\n\n{e}")

    tooltip(f"Testing {engine_label(c)}...")
    mw.taskman.run_in_background(lambda: engines.translate(c, *sample), done)


def setup_main_menu():
    action = QAction(f"{ADDON_NAME}: test engine", mw)
    action.triggered.connect(test_engine)
    mw.form.menuTools.addAction(action)


gui_hooks.browser_will_show.append(setup_browser_menu)
gui_hooks.editor_did_init_buttons.append(setup_editor_button)
setup_main_menu()

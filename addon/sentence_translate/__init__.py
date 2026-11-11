"""Sentence Translate: translate the Sentence field to English with the target word in bold."""
from anki import hooks
from aqt import gui_hooks, mw
from aqt.operations import CollectionOp, QueryOp
from aqt.operations.note import update_note
from aqt.qt import QAction, QMessageBox, QTimer
from aqt.utils import showInfo, showWarning, tooltip

from . import engines, picture_ui

ADDON_NAME = "Sentence Translate"
LANGUAGE_CODES = {
    "english": "EN", "swedish": "SV", "german": "DE", "french": "FR", "spanish": "ES", "italian": "IT",
    "portuguese": "PT", "russian": "RU", "dutch": "NL", "polish": "PL", "norwegian": "NO", "danish": "DA",
    "finnish": "FI", "ukrainian": "UK", "turkish": "TR", "japanese": "JA", "chinese": "ZH", "korean": "KO",
}


def cfg():
    """User config on top of the defaults, so keys added in an update are always present."""
    defaults = mw.addonManager.addonConfigDefaults(__name__) or {}
    return {**defaults, **(mw.addonManager.getConfig(__name__) or {})}


def engine_label(c):
    e = c.get("ENGINE", "claude")
    model = {"claude": c.get("CLAUDE_MODEL"), "gemini": c.get("GEMINI_MODEL"), "ollama": c.get("OLLAMA_MODEL"),
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
    action = QAction(f"Translate Sentence ({engines.languages(cfg())[1]})", browser)
    keys = cfg().get("BROWSER_SHORTCUT")
    if keys:
        action.setShortcut(keys)
    action.triggered.connect(lambda: run_browser(browser))
    browser.form.menu_Cards.addSeparator()
    browser.form.menu_Cards.addAction(action)

    pic = QAction(f"Add picture of the word ({cfg().get('PICTURE_FIELD') or 'Picture'})", browser)
    keys = cfg().get("PICTURE_SHORTCUT")
    if keys:
        pic.setShortcut(keys)
    pic.triggered.connect(lambda: picture_ui.run_picture(browser))
    browser.form.menu_Cards.addAction(pic)


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
    target = engines.languages(cfg())[1]
    tip = f"Translate Sentence into {target}" + (f" ({keys})" if keys else "")
    label = LANGUAGE_CODES.get(target.lower(), "TR")
    buttons.append(editor.addButton(None, "sentence_translate", on_editor_saved, tip=tip, label=label, keys=keys))


# ---- New notes (Yomitan via AnkiConnect, the Add window): translate in the background ----

_queue = []  # note ids waiting for translation
_running = False


def on_note_will_be_added(col, note, deck_id):
    c = cfg()
    if not c.get("AUTO_TRANSLATE_NEW") or note_input(c, note) is None:
        return
    if engines.plain(note[c["TARGET_FIELD"]]):
        return
    # The note has no id until it is added; look at it right after the add finishes.
    mw.taskman.run_on_main(lambda: QTimer.singleShot(200, lambda: _enqueue(note)))


def _enqueue(note):
    if note.id:
        _queue.append(note.id)
        _drain()


def _drain():
    global _running
    if _running or not _queue or mw.col is None:
        return
    nid = _queue.pop(0)
    c = cfg()
    try:
        note = mw.col.get_note(nid)
    except Exception:
        return _drain()
    inp = note_input(c, note)
    if inp is None or engines.plain(note[c["TARGET_FIELD"]]):
        return _drain()
    _running = True

    def done(fut):
        global _running
        _running = False
        try:
            text = fut.result()
        except Exception as e:
            print(f"{ADDON_NAME}: new note {nid}: {e}")
            tooltip(f"{ADDON_NAME}: translation failed for a new note ({str(e)[:80]})")
            return _drain()
        try:
            fresh = mw.col.get_note(nid)
        except Exception:
            return _drain()
        if not engines.plain(fresh[c["TARGET_FIELD"]]):  # the user may have typed one meanwhile
            fresh[c["TARGET_FIELD"]] = text
            if c.get("TAG"):
                fresh.add_tag(c["TAG"])
            update_note(parent=mw, note=fresh).run_in_background()
        _drain()

    mw.taskman.run_in_background(lambda: translate_one(c, inp), done)


# ---- Tools menu and config ----

def open_settings():
    from .settings import open_settings as show

    show(__name__)


def setup_main_menu():
    action = QAction(f"{ADDON_NAME} settings...", mw)
    action.triggered.connect(open_settings)
    mw.form.menuTools.addAction(action)
    picture_ui.setup_tools_menu()


hooks.note_will_be_added.append(on_note_will_be_added)
gui_hooks.browser_will_show.append(setup_browser_menu)
gui_hooks.editor_did_init_buttons.append(setup_editor_button)
mw.addonManager.setConfigAction(__name__, open_settings)
setup_main_menu()
picture_ui.register_hooks()

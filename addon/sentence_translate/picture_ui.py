"""Pictures in Anki: the Browse action, the one-time setup window and the picture server's lifetime."""
import html
import os
import re
import shutil
import time

from aqt import gui_hooks, mw
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import (
    QAction,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTimer,
    QVBoxLayout,
)
from aqt.utils import askUser, openFolder, showInfo, showWarning, tooltip

from . import engines, picture
from . import picture_setup as ps

ADDON = __name__.rsplit(".", 1)[0]
TITLE = "Sentence Translate pictures"
_server_proc = None


def cfg():
    defaults = mw.addonManager.addonConfigDefaults(ADDON) or {}
    return {**defaults, **(mw.addonManager.getConfig(ADDON) or {})}


def install_root(c):
    return (c.get("PICTURE_INSTALL_DIR") or "").strip() or ps.default_dir()


def field_name(c):
    return c.get("PICTURE_FIELD") or "Picture"


# ---- which notes need a picture ----

def picture_input(c, note):
    """(sentence, expression, translation, meaning) or None if the note lacks the fields."""
    for f in (c["SOURCE_FIELD"], c["EXPRESSION_FIELD"], field_name(c)):
        if f not in note:
            return None
    sentence = engines.clean_sentence(note[c["SOURCE_FIELD"]])
    expression = engines.plain(note[c["EXPRESSION_FIELD"]])
    if not sentence or not expression:
        return None
    get = lambda name: note[name] if name and name in note else ""
    return sentence, expression, get(c["TARGET_FIELD"]), engines.plain(get(c.get("MEANING_FIELD")))


def has_picture(c, note):
    """True if the field has an image that counts (images matching PICTURE_EMPTY_IF_FILE_CONTAINS do not)."""
    srcs = re.findall(r"<img[^>]*?src=[\"']?([^\"' >]+)", note[field_name(c)] or "", re.I)
    ignore = [p.strip() for p in (c.get("PICTURE_EMPTY_IF_FILE_CONTAINS") or "").split(",") if p.strip()]
    return any(not any(p in s for p in ignore) for s in srcs)


def ask_overwrite(parent, field, count, total):
    box = QMessageBox(parent)
    box.setWindowTitle(TITLE)
    box.setText(f"{count} of {total} notes already have a picture in {field}.\n\nReplace those pictures?")
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


# ---- Browse > Cards > Add picture of the word ----

def run_picture(browser):
    c = cfg()
    if not ps.health(picture.server_url(c)) and not ps.is_installed(install_root(c)):
        if askUser("Pictures need a local image model and text model, and they are not set up yet.\n\n"
                   "Set them up now? (One time, about 25 GB of downloads.)", parent=browser):
            open_setup(browser)
        return
    field = field_name(c)
    nids = list(browser.selected_notes())
    if not nids:
        tooltip("No notes selected.")
        return
    todo, existing, skipped = [], [], 0
    for nid in nids:
        note = mw.col.get_note(nid)
        inp = picture_input(c, note)
        if inp is None:
            skipped += 1
            continue
        (existing if has_picture(c, note) else todo).append((nid, inp))
    if existing:
        choice = ask_overwrite(browser, field, len(existing), len(todo) + len(existing))
        if choice is None:
            return
        if choice == "replace":
            todo += existing
    if not todo:
        showInfo(f"Nothing to do. Skipped {len(existing)} with a picture and {skipped} without the fields or a sentence.")
        return

    scenes = set()  # notes whose word cannot be pictured: they get the sentence's scene

    def work(_col):
        global _server_proc
        prompts, images, failed, not_depictable = [], [], [], []
        total = len(todo)
        progress = lambda **kw: mw.taskman.run_on_main(lambda: mw.progress.update(**kw))
        # Phase 1: descriptions from the text model. Phase 2: pictures. The two models never share the GPU.
        ps.unload(picture.server_url(c))
        for i, (nid, inp) in enumerate(todo):
            if mw.progress.want_cancel():
                return images, failed, not_depictable
            progress(label=f"Describing {i + 1} of {total}", value=i, max=total * 2)
            try:
                p, kind = picture.picture_prompt(c, *inp)
            except Exception as e:
                print(f"{TITLE}: prompt for note {nid}: {e}")
                failed.append((nid, str(e)))
                if not prompts and len(failed) >= 3:
                    return images, failed, not_depictable
                continue
            if p is None:
                not_depictable.append(nid)
            else:
                if kind == "scene":
                    scenes.add(nid)
                prompts.append((nid, inp[1], p))
        picture.unload_prompt_model(c)
        if not prompts:
            return images, failed, not_depictable
        progress(label="Starting the picture server...")
        try:
            proc = ps.start(install_root(c), picture.server_url(c), print, mw.progress.want_cancel,
                            idle_minutes=c.get("PICTURE_IDLE_MINUTES") or 10)
            if proc is not None:
                _server_proc = proc
        except ps.Cancelled:
            return images, failed, not_depictable
        except Exception as e:
            failed.append((None, str(e)))
            return images, failed, not_depictable
        for i, (nid, word, p) in enumerate(prompts):
            if mw.progress.want_cancel():
                break
            label = f"Drawing {i + 1} of {len(prompts)}" + (" (loading the image model first)" if i == 0 else "")
            progress(label=label, value=total + i, max=total + len(prompts))
            try:
                data, ext = picture.generate(c, p)
                images.append((nid, word, p, data, ext))
            except Exception as e:
                print(f"{TITLE}: picture for note {nid}: {e}")
                failed.append((nid, str(e)))
                if not images and len(failed) >= 2:
                    break
        return images, failed, not_depictable

    def on_done(result):
        images, failed, not_depictable = result
        if not images:
            msg = failed[0][1] if failed else (
                f"None of the {len(not_depictable)} words could be pictured." if not_depictable else "Cancelled.")
            showWarning(f"No pictures were added.\n\n{msg}")
            return

        def write(col):
            notes = []
            for nid, word, p, data, ext in images:
                safe = "".join(ch for ch in word if ch.isalnum())[:20] or "word"
                name = col.media.write_data(f"picture_{safe}_{nid}_{int(time.time())}.{ext}", data)
                note = col.get_note(nid)
                note[field] = f'<img src="{name}" alt="{html.escape(p, quote=True)}">'
                if c.get("PICTURE_TAG"):
                    note.add_tag(c["PICTURE_TAG"])
                notes.append(note)
            return col.update_notes(notes)

        def finished(_changes):
            msg = f"Added {len(images)} pictures to {field}."
            n_scene = sum(1 for nid, *_ in images if nid in scenes)
            if n_scene:
                msg += f"\n{n_scene} of them show the sentence's scene, because the word itself cannot be pictured."
            if not_depictable:
                msg += f"\n{len(not_depictable)} words could not be pictured and were left as they were."
            if failed:
                msg += f"\n{len(failed)} failed, for example:\n{failed[0][1][:300]}"
            showInfo(msg + "\n\nEdit > Undo reverts the whole run.")

        CollectionOp(browser, write).success(finished).run_in_background()

    QueryOp(parent=browser, op=work, success=on_done).with_progress("Making pictures...").run_in_background()


# ---- one-time setup ----

def free_space(path):
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return shutil.disk_usage(path or os.path.expanduser("~")).free


class SetupDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.c = cfg()
        self.root = install_root(self.c)
        self.cancel = False
        self.setWindowTitle("Set up pictures")
        self.resize(660, 500)
        layout = QVBoxLayout(self)
        gpu = ps.gpu_info()
        gpu_line = (f"GPU: {gpu[0]} ({gpu[1] // 1024} GB). About 5 seconds per picture." if gpu
                    else "No NVIDIA GPU was found. The picture model needs one.")
        self.info = QLabel(
            "Sentence Translate can draw a small picture of the word for notes that have none, for example "
            "sentences from books. Everything runs on your own computer.\n\n"
            "What happens:\n"
            f"  1. Checks Ollama and downloads the text model {self.c.get('PICTURE_PROMPT_MODEL') or 'gemma4:12b'} "
            "(about 8 GB) if it is missing; it describes the picture\n"
            "  2. Gets uv, a Python package manager (github.com/astral-sh/uv)\n"
            "  3. Installs Python packages including PyTorch and diffusers, about 4 GB\n"
            "  4. Downloads FLUX.1 [schnell] in fp8 (Apache 2.0, huggingface.co, about 17 GB)\n\n"
            f"Install folder: {self.root}\nThe model files go to the Hugging Face cache in your user folder.\n"
            f"{gpu_line}\n\nYou can cancel and run it again later; it continues where it stopped."
        )
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        layout.addWidget(self.log, 1)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.go = QPushButton("Install")
        self.go.setEnabled(bool(gpu))
        self.close_button = QPushButton("Cancel")
        row.addWidget(self.go)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.go.clicked.connect(self.begin)
        self.close_button.clicked.connect(self.reject)

    def reject(self):
        self.cancel = True
        super().reject()

    def begin(self):
        free = free_space(os.path.expanduser("~"))
        if free < 30 * (1 << 30) and not askUser(f"Only {free // (1 << 30)} GB of disk space is free. Continue anyway?",
                                                 parent=self):
            return
        self.go.setEnabled(False)
        self.log.setVisible(True)
        self.bar.setVisible(True)
        self.bar.setRange(0, ps.INSTALL_STEPS)
        self.info.setText("Installing... You can keep using Anki, but do not close it until this is finished.")
        c = self.c

        def log(line):
            mw.taskman.run_on_main(lambda: self.log.appendPlainText(line))

        def step(n, total, label):
            def ui():
                self.log.appendPlainText(f"\n== Step {n} of {total}: {label} ==")
                self.bar.setValue(n - 1)
                self.info.setText(f"Step {n} of {total}: {label}")

            mw.taskman.run_on_main(ui)

        def task():
            ps.install(self.root, log, lambda: self.cancel, step, c.get("OLLAMA_URL") or "http://127.0.0.1:11434",
                       c.get("PICTURE_PROMPT_MODEL") or "gemma4:12b")
            log("\nStarting the picture server...")
            return ps.start(self.root, picture.server_url(c), log, lambda: self.cancel,
                            idle_minutes=c.get("PICTURE_IDLE_MINUTES") or 10)

        def done(future):
            global _server_proc
            self.close_button.setText("Close")
            try:
                proc = future.result()
            except ps.Cancelled:
                self.info.setText("Cancelled. You can run the setup again from Tools > Sentence Translate: set up pictures.")
                self.go.setEnabled(True)
                return
            except Exception as e:
                self.log.appendPlainText(f"\nERROR: {e}")
                self.info.setText("Something went wrong (see the log). You can run the setup again; "
                                  "it continues where it stopped.")
                self.go.setEnabled(True)
                return
            if proc is not None:
                _server_proc = proc
            self.bar.setValue(self.bar.maximum())
            self.info.setText(
                "Done!\n\nSelect a few notes in the card browser and use Cards > Add picture of the word. "
                "Look at the results before running it on many notes.\n"
                "The image model loads on the first picture (about 10 s) and frees the GPU again when it has been idle."
            )

        mw.taskman.run_in_background(task, done, uses_collection=False)


_dialog = None


def open_setup(parent=None):
    global _dialog
    if _dialog is not None and _dialog.isVisible():
        _dialog.raise_()
        return
    _dialog = SetupDialog(parent or mw)
    _dialog.show()


def welcome():
    c = cfg()
    if c.get("PICTURE_ONBOARDING_SHOWN") or ps.is_installed(install_root(c)) or ps.health(picture.server_url(c)):
        return
    c["PICTURE_ONBOARDING_SHOWN"] = True
    mw.addonManager.writeConfig(ADDON, c)
    if ps.gpu_info() is None:
        return  # no NVIDIA GPU: the feature cannot run here, so do not advertise it
    if askUser(
        "New in Sentence Translate: pictures!\n\n"
        "It can draw a small picture of the target word for notes that have no picture, such as sentences from books. "
        "A text model and an image model run on your own computer; they must be downloaded once (about 25 GB).\n\n"
        "Set it up now? You can also do it later from Tools > Sentence Translate: set up pictures."
    ):
        open_setup(mw)


def stop_server():
    global _server_proc
    ps.stop(picture.server_url(cfg()), _server_proc)
    _server_proc = None


def setup_tools_menu():
    a = QAction("Sentence Translate: set up pictures...", mw)
    a.triggered.connect(lambda: open_setup(mw))
    mw.form.menuTools.addAction(a)
    b = QAction("Sentence Translate: open picture server folder", mw)
    b.triggered.connect(lambda: openFolder(install_root(cfg())))
    mw.form.menuTools.addAction(b)


def register_hooks():
    gui_hooks.profile_did_open.append(lambda: QTimer.singleShot(2500, welcome))
    gui_hooks.profile_will_close.append(stop_server)

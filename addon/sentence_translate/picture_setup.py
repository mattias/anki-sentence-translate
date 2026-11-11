"""Install and run the local picture server (FLUX.1 [schnell]) and the Ollama text model.

No Anki imports here, so the module can be tested on its own. Everything lives in one folder (default: a folder in
the user's local data directory); the model weights go to the normal Hugging Face cache, so they are shared with
other programs that already downloaded them.
"""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(HERE, "picture_server")
SERVER_FILES = ("server.py", "download.py", "pyproject.toml", "uv.lock")
UV_BASE_URL = "https://github.com/astral-sh/uv/releases/latest/download/"
OLLAMA_URL = "https://ollama.com/download"
IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000
INSTALL_STEPS = 5


class Cancelled(Exception):
    pass


class SetupError(Exception):
    pass


def default_dir():
    if IS_WINDOWS:
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SentenceTranslatePictures")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/SentenceTranslatePictures")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "sentence-translate-pictures")


class Paths:
    def __init__(self, root):
        self.root = root
        self.server = os.path.join(root, "server")
        self.uv_dir = os.path.join(root, "uv")
        self.log = os.path.join(root, "server.log")
        self.marker = os.path.join(root, "installed.json")
        exe = "Scripts" if IS_WINDOWS else "bin"
        self.venv_python = os.path.join(self.server, ".venv", exe, "python.exe" if IS_WINDOWS else "python")


def bundle_version():
    """Hash of the dependency files: a change means the Python packages must be synced again."""
    h = hashlib.sha256()
    for name in ("pyproject.toml",):
        with open(os.path.join(BUNDLED, name), "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def is_installed(root):
    p = Paths(root)
    try:
        with open(p.marker, encoding="utf-8") as f:
            info = json.load(f)
    except Exception:
        return False
    return info.get("version") == bundle_version() and info.get("models") and os.path.exists(p.venv_python)


def clean_env():
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "VIRTUAL_ENV", "PYTHONEXECUTABLE"):
        env.pop(key, None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def kill_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=15)
        else:
            proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run(cmd, cwd, env, log, cancelled):
    """Run a command, stream its output to log(line), kill it if cancelled() becomes true."""
    kwargs = {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {"start_new_session": True}
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", **kwargs)
    lines = queue.Queue()

    def reader():
        for line in proc.stdout:
            lines.put(line.rstrip())
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    tail = []
    while True:
        if cancelled():
            kill_tree(proc)
            raise Cancelled()
        try:
            line = lines.get(timeout=0.3)
        except queue.Empty:
            continue
        if line is None:
            break
        tail = (tail + [line])[-15:]
        if line:
            log(line)
    code = proc.wait()
    if code != 0:
        raise SetupError(f"Command failed ({code}): {' '.join(cmd[:4])}\n" + "\n".join(tail[-8:]))


def download(url, dest, log, cancelled):
    req = urllib.request.Request(url, headers={"User-Agent": "sentence-translate-anki-addon"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while True:
            if cancelled():
                raise Cancelled()
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


def uv_archive_name():
    import platform

    arm = platform.machine().lower() in ("arm64", "aarch64")
    if IS_WINDOWS:
        return "uv-aarch64-pc-windows-msvc.zip" if arm else "uv-x86_64-pc-windows-msvc.zip"
    if sys.platform == "darwin":
        return "uv-aarch64-apple-darwin.tar.gz" if arm else "uv-x86_64-apple-darwin.tar.gz"
    return "uv-aarch64-unknown-linux-gnu.tar.gz" if arm else "uv-x86_64-unknown-linux-gnu.tar.gz"


def ensure_uv(paths, log, cancelled):
    exe = "uv.exe" if IS_WINDOWS else "uv"
    local = os.path.join(paths.uv_dir, exe)
    found = local if os.path.exists(local) else shutil.which("uv")
    if found:
        log(f"Using uv: {found}")
        return found
    log("Downloading uv (the Python package manager the server needs)...")
    os.makedirs(paths.uv_dir, exist_ok=True)
    name = uv_archive_name()
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, name)
        download(UV_BASE_URL + name, archive, log, cancelled)
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
        else:
            with tarfile.open(archive) as t:
                t.extractall(tmp)
        for folder, _dirs, files in os.walk(tmp):
            if exe in files:
                shutil.copy2(os.path.join(folder, exe), local)
                os.chmod(local, 0o755)
                return local
    raise SetupError("Could not find uv in the downloaded archive.")


def gpu_info():
    """(name, total MB) of the first NVIDIA GPU, or None."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0)
        name, mem = out.stdout.strip().splitlines()[0].rsplit(",", 1)
        return name.strip(), int(mem)
    except Exception:
        return None


def copy_server(paths, log=None):
    """Copy the bundled server files; returns True if any file changed."""
    os.makedirs(paths.server, exist_ok=True)
    changed = False
    for name in SERVER_FILES:
        src, dst = os.path.join(BUNDLED, name), os.path.join(paths.server, name)
        if not os.path.exists(src):
            continue
        with open(src, "rb") as f:
            new = f.read()
        old = open(dst, "rb").read() if os.path.exists(dst) else None
        if new != old:
            with open(dst, "wb") as f:
                f.write(new)
            changed = True
    if changed and log:
        log("Copied the picture server files.")
    return changed


# ---- Ollama (the text model that describes the picture) ----

def ollama_ok(url):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=3) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return None


def has_model(names, model):
    want = model if ":" in model else model + ":latest"
    return any(n == want or n == model for n in names or [])


def pull_model(url, model, log, cancelled):
    req = urllib.request.Request(url.rstrip("/") + "/api/pull", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"})
    last, next_report = "", 0
    with urllib.request.urlopen(req, timeout=3600) as r:
        for raw in r:
            if cancelled():
                raise Cancelled()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("error"):
                raise SetupError(f"Ollama could not download {model}: {msg['error']}")
            status = msg.get("status", "")
            total, done = msg.get("total"), msg.get("completed")
            if total and done:
                pct = int(100 * done / total)
                if pct >= next_report:
                    log(f"  {status}: {done // (1 << 20)} of {total // (1 << 20)} MB")
                    next_report = pct + 10
            elif status != last:
                log(f"  {status}")
                next_report = 0
            last = status


# ---- install, start, stop ----

def install(root, log, cancelled, step, ollama_url, prompt_model):
    """One-time setup. step(n, total, label) reports progress. Safe to run again after a failure."""
    paths = Paths(root)
    os.makedirs(root, exist_ok=True)
    log(f"Install folder: {root}")
    gpu = gpu_info()
    log(f"GPU: {gpu[0]} ({gpu[1] // 1024} GB)" if gpu else "No NVIDIA GPU found.")
    if not gpu:
        raise SetupError("The picture server needs an NVIDIA GPU (FLUX would take many minutes per picture on the CPU).")

    step(1, INSTALL_STEPS, f"Checking Ollama and the text model {prompt_model}")
    names = ollama_ok(ollama_url)
    if names is None:
        raise SetupError(f"Ollama is not running at {ollama_url}. Install it from {OLLAMA_URL}, start it, "
                         "and run this setup again.")
    if has_model(names, prompt_model):
        log(f"{prompt_model} is already in Ollama.")
    else:
        log(f"Downloading {prompt_model} into Ollama (about 8 GB)...")
        pull_model(ollama_url, prompt_model, log, cancelled)

    step(2, INSTALL_STEPS, "Getting uv")
    uv = ensure_uv(paths, log, cancelled)
    env = clean_env()

    step(3, INSTALL_STEPS, "Installing Python packages (PyTorch and diffusers, about 4 GB)")
    copy_server(paths, log)
    run([uv, "sync", "--python", "3.12"], paths.server, env, log, cancelled)

    step(4, INSTALL_STEPS, "Downloading FLUX.1 [schnell] (about 17 GB, from huggingface.co)")
    run([paths.venv_python, "download.py"], paths.server, env, log, cancelled)

    with open(paths.marker, "w", encoding="utf-8") as f:
        json.dump({"version": bundle_version(), "models": True}, f)
    step(INSTALL_STEPS, INSTALL_STEPS, "Done")


def health(url, timeout=2):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def unload(url):
    """Ask a running picture server to free the GPU (the text model needs it). Harmless if none runs."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/unload", data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30):
            pass
    except Exception:
        pass


def stop(url, proc=None):
    """Stop the server: kill our process, and also one left from an earlier Anki session."""
    kill_tree(proc)
    if health(url):
        try:
            req = urllib.request.Request(url.rstrip("/") + "/shutdown", data=b"{}")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


def port_of(url):
    tail = url.rsplit(":", 1)[-1].split("/")[0]
    return int(tail) if tail.isdigit() else 8189


def log_tail(paths, n=12):
    try:
        with open(paths.log, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return ""


def start(root, url, log, cancelled, idle_minutes=10, timeout=120):
    """Start the server on 127.0.0.1 and wait until it answers. Returns the process (None if one already runs).

    The model loads on the first picture and is freed again after idle_minutes, so a running server costs no GPU memory.
    """
    paths = Paths(root)
    if health(url):
        return None
    if not is_installed(root):
        raise SetupError("The picture server is not set up yet.")
    copy_server(paths)  # pick up a newer server.py after an add-on update
    cmd = [paths.venv_python, "server.py", "--host", "127.0.0.1", "--port", str(port_of(url)),
           "--idle-minutes", str(idle_minutes), "--parent-pid", str(os.getpid())]
    logfile = open(paths.log, "w", encoding="utf-8")
    kwargs = {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {"start_new_session": True}
    proc = subprocess.Popen(cmd, cwd=paths.server, env=clean_env(), stdout=logfile, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **kwargs)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancelled():
            kill_tree(proc)
            raise Cancelled()
        if proc.poll() is not None:
            raise SetupError("The picture server stopped while starting.\n\n" + log_tail(paths))
        if health(url):
            log("Picture server is ready.")
            return proc
        time.sleep(1)
    kill_tree(proc)
    raise SetupError("The picture server did not start in time.\n\n" + log_tail(paths))

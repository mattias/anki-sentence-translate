"""Build sentence_translate.ankiaddon from addon/sentence_translate (run: python build.py)."""
import os
import zipfile

src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "addon", "sentence_translate")
out = os.path.join(os.path.dirname(src), "..", "sentence_translate.ankiaddon")
with zipfile.ZipFile(os.path.normpath(out), "w", zipfile.ZIP_DEFLATED) as z:
    for folder, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".pyc"):
                path = os.path.join(folder, name)
                z.write(path, os.path.relpath(path, src))
print("built", os.path.normpath(out))

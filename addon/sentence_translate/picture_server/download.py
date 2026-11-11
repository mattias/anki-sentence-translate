"""Download FLUX.1 [schnell] in fp8 from ungated Hugging Face repos (about 17 GB). Safe to run again."""
import os
import threading
import time

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from huggingface_hub import constants, hf_hub_download  # noqa: E402

FILES = [
    ("kijai/flux-fp8", "flux1-schnell-fp8-e4m3fn.safetensors", 11.9),
    ("kijai/flux-fp8", "flux-vae-bf16.safetensors", 0.2),
    ("comfyanonymous/flux_text_encoders", "t5xxl_fp8_e4m3fn.safetensors", 4.9),
    ("comfyanonymous/flux_text_encoders", "clip_l.safetensors", 0.2),
    ("openai/clip-vit-large-patch14", "config.json", 0),
    ("openai/clip-vit-large-patch14", "tokenizer_config.json", 0),
    ("openai/clip-vit-large-patch14", "vocab.json", 0),
    ("openai/clip-vit-large-patch14", "merges.txt", 0),
    ("openai/clip-vit-large-patch14", "special_tokens_map.json", 0),
    ("google/t5-v1_1-xxl", "config.json", 0),
    ("google/t5-v1_1-xxl", "spiece.model", 0),
    ("google/t5-v1_1-xxl", "tokenizer_config.json", 0),
    ("google/t5-v1_1-xxl", "special_tokens_map.json", 0),
]


def folder_gb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1e9


def main():
    cache = constants.HF_HUB_CACHE
    print(f"Hugging Face cache: {cache}", flush=True)
    for repo, name, gb in FILES:
        try:
            hf_hub_download(repo, name, local_files_only=True)
            continue
        except Exception:
            pass
        if gb:
            print(f"Downloading {name} from {repo} (about {gb} GB)...", flush=True)
        folder = os.path.join(cache, "models--" + repo.replace("/", "--"))
        stop = threading.Event()

        def report():
            start = folder_gb(folder) if os.path.isdir(folder) else 0
            while not stop.wait(15):
                print(f"  {folder_gb(folder) - start:.1f} of about {gb} GB", flush=True)

        if gb >= 1:
            threading.Thread(target=report, daemon=True).start()
        try:
            hf_hub_download(repo, name)
        finally:
            stop.set()
    print("All model files are downloaded.", flush=True)


if __name__ == "__main__":
    t = time.time()
    main()
    print(f"({time.time() - t:.0f} s)", flush=True)

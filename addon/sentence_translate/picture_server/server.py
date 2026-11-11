"""Local picture server for the Sentence Translate Anki add-on.

FLUX.1 [schnell] (Apache 2.0) in fp8 via diffusers. The weights stay in fp8 in memory and are cast to bf16
layer by layer while the model runs; blocks are streamed to the GPU, so it fits next to other GPU programs.

    uv run python server.py [--port 8189] [--idle-minutes 10]

POST /generate {"prompt": "...", "size": 512, "steps": 4, "seed": 0, "format": "webp"}
  -> {"image": "<base64>", "ext": "webp", "seconds": 3.2}
GET /health -> {"ok": true, "loaded": false}
"""
import argparse
import base64
import gc
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # the setup downloads everything; never reach the network while running
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import torch  # noqa: E402
from huggingface_hub import hf_hub_download  # noqa: E402
from safetensors import safe_open  # noqa: E402

HERE = Path(__file__).parent
DEVICE = torch.device("cuda")
DTYPE = torch.bfloat16
FP8 = torch.float8_e4m3fn

FILES = {
    "transformer": ("kijai/flux-fp8", "flux1-schnell-fp8-e4m3fn.safetensors"),
    "vae": ("kijai/flux-fp8", "flux-vae-bf16.safetensors"),
    "t5": ("comfyanonymous/flux_text_encoders", "t5xxl_fp8_e4m3fn.safetensors"),
    "clip": ("comfyanonymous/flux_text_encoders", "clip_l.safetensors"),
}
CLIP_REPO = "openai/clip-vit-large-patch14"
T5_REPO = "google/t5-v1_1-xxl"

TRANSFORMER_CONFIG = {
    "attention_head_dim": 128, "axes_dims_rope": [16, 56, 56], "guidance_embeds": False, "in_channels": 64,
    "joint_attention_dim": 4096, "num_attention_heads": 24, "num_layers": 19, "num_single_layers": 38,
    "patch_size": 1, "pooled_projection_dim": 768,
}
VAE_CONFIG = {
    "act_fn": "silu", "block_out_channels": [128, 256, 512, 512],
    "down_block_types": ["DownEncoderBlock2D"] * 4, "up_block_types": ["UpDecoderBlock2D"] * 4,
    "in_channels": 3, "out_channels": 3, "latent_channels": 16, "layers_per_block": 2, "norm_num_groups": 32,
    "sample_size": 1024, "scaling_factor": 0.3611, "shift_factor": 0.1159,
    "use_quant_conv": False, "use_post_quant_conv": False,
}


def path(key):
    return hf_hub_download(*FILES[key])


def read_safetensors(file):
    with safe_open(file, framework="pt") as f:
        return {k: f.get_tensor(k) for k in f.keys()}


def fp8_linears(model, keep_bf16=()):
    """Keep Linear weights in fp8 (storage) and cast them to bf16 at each forward; everything else in bf16.

    keep_bf16: name patterns of Linear layers that must stay bf16 (T5's `wo` casts its input to the weight dtype).
    """
    from diffusers.hooks import apply_layerwise_casting

    for mname, module in model.named_modules():
        bf16_linear = isinstance(module, torch.nn.Linear) and any(mname.endswith(k) for k in keep_bf16)
        for name, p in module.named_parameters(recurse=False):
            if bf16_linear or not (isinstance(module, torch.nn.Linear) and name == "weight"):
                if p.dtype != DTYPE:
                    setattr(module, name, torch.nn.Parameter(p.to(DTYPE), requires_grad=False))
    apply_layerwise_casting(model, storage_dtype=FP8, compute_dtype=DTYPE, skip_modules_pattern=tuple(keep_bf16),
                            skip_modules_classes=(torch.nn.Conv1d, torch.nn.Conv2d, torch.nn.Conv3d, torch.nn.LayerNorm,
                                                  torch.nn.GroupNorm, torch.nn.Embedding))


def load_pipeline(log=print):
    from accelerate import init_empty_weights
    from diffusers import AutoencoderKL, FlowMatchEulerDiscreteScheduler, FluxPipeline, FluxTransformer2DModel
    from diffusers.hooks import apply_group_offloading
    from diffusers.loaders.single_file_utils import convert_flux_transformer_checkpoint_to_diffusers
    from diffusers.loaders.single_file_utils import convert_ldm_vae_checkpoint
    from transformers import CLIPTextConfig, CLIPTextModel, CLIPTokenizer, T5Config, T5EncoderModel, T5TokenizerFast

    t = time.time()
    log("loading transformer (fp8)...")
    sd = convert_flux_transformer_checkpoint_to_diffusers(read_safetensors(path("transformer")))
    with init_empty_weights():
        transformer = FluxTransformer2DModel.from_config(TRANSFORMER_CONFIG)
    missing, unexpected = transformer.load_state_dict(sd, strict=False, assign=True)
    if missing:
        raise RuntimeError(f"transformer: missing {missing[:5]}")
    del sd
    transformer.eval().requires_grad_(False)
    fp8_linears(transformer)
    apply_group_offloading(transformer, onload_device=DEVICE, offload_type="block_level", num_blocks_per_group=1, low_cpu_mem_usage=True,
                           use_stream=True)

    log("loading T5 (fp8)...")
    t5_config = T5Config.from_pretrained(T5_REPO)
    with init_empty_weights():
        t5 = T5EncoderModel(t5_config)
    sd = read_safetensors(path("t5"))
    if "encoder.embed_tokens.weight" not in sd and "shared.weight" in sd:
        sd["encoder.embed_tokens.weight"] = sd["shared.weight"]
    missing, _ = t5.load_state_dict(sd, strict=False, assign=True)
    if [m for m in missing if "embed_tokens" not in m and "shared" not in m]:
        raise RuntimeError(f"T5: missing {missing[:5]}")
    del sd
    t5.eval().requires_grad_(False)
    fp8_linears(t5, keep_bf16=(".wo",))
    t5.to(DEVICE)  # about 5 GB in fp8

    log("loading CLIP and VAE...")
    clip_config = CLIPTextConfig.from_pretrained(CLIP_REPO)
    clip = CLIPTextModel(clip_config)
    clip_sd = read_safetensors(path("clip"))
    wanted = set(clip.state_dict())
    if not (set(clip_sd) & wanted):  # newer transformers have no "text_model." prefix
        clip_sd = {k.removeprefix("text_model."): v for k, v in clip_sd.items()}
    missing, _ = clip.load_state_dict(clip_sd, strict=False)
    if [m for m in missing if "position_ids" not in m]:
        raise RuntimeError(f"CLIP: missing {missing[:5]}")
    clip.to(DEVICE, DTYPE).eval().requires_grad_(False)

    vae = AutoencoderKL.from_config(VAE_CONFIG)
    vae_sd = read_safetensors(path("vae"))
    if any(k.startswith("encoder.down.") or k.startswith("decoder.up.") for k in vae_sd):
        vae_sd = convert_ldm_vae_checkpoint(vae_sd, vae.config)
    vae.load_state_dict(vae_sd)
    vae.to(DEVICE, DTYPE).eval().requires_grad_(False)

    pipe = FluxPipeline(
        scheduler=FlowMatchEulerDiscreteScheduler(num_train_timesteps=1000, shift=1.0, use_dynamic_shifting=False),
        text_encoder=clip, tokenizer=CLIPTokenizer.from_pretrained(CLIP_REPO),
        text_encoder_2=t5, tokenizer_2=T5TokenizerFast.from_pretrained(T5_REPO),
        vae=vae, transformer=transformer,
    )
    pipe.set_progress_bar_config(disable=True)
    log(f"ready in {time.time() - t:.0f} s")
    return pipe


class Generator:
    def __init__(self, idle_minutes):
        self.pipe = None
        self.lock = threading.Lock()
        self.last_used = time.time()
        self.idle = idle_minutes * 60
        threading.Thread(target=self._unload_when_idle, daemon=True).start()

    def unload(self, why):
        with self.lock:
            if self.pipe is not None:
                self.pipe = None
                gc.collect()
                torch.cuda.empty_cache()
                print(f"unloaded ({why})", flush=True)

    def _unload_when_idle(self):
        while True:
            time.sleep(30)
            if self.idle and self.pipe is not None and time.time() - self.last_used > self.idle:
                self.unload("idle")

    def generate(self, prompt, size=512, steps=4, seed=None, fmt="webp", quality=80):
        with self.lock:
            if self.pipe is None:
                self.pipe = load_pipeline(lambda m: print(m, flush=True))
            t = time.time()
            gen = torch.Generator("cpu").manual_seed(int(seed if seed is not None else time.time_ns() % 2**31))
            with torch.inference_mode():
                img = self.pipe(prompt, height=size, width=size, num_inference_steps=steps, guidance_scale=0.0,
                                max_sequence_length=256, generator=gen).images[0]
            self.last_used = time.time()
        buf = io.BytesIO()
        fmt = fmt.lower()
        if fmt == "webp":
            img.save(buf, "WEBP", quality=quality, method=6)
        elif fmt in ("jpg", "jpeg"):
            img.save(buf, "JPEG", quality=quality)
            fmt = "jpg"
        else:
            img.save(buf, "PNG")
            fmt = "png"
        return buf.getvalue(), fmt, time.time() - t


def make_handler(gen):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"ok": True, "loaded": gen.pipe is not None})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/shutdown":
                self._send(200, {"ok": True})
                threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
                return
            if self.path == "/unload":  # the add-on frees the GPU before the text model runs
                gen.unload("asked")
                return self._send(200, {"ok": True})
            if self.path != "/generate":
                return self._send(404, {"error": "not found"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                prompt = (req.get("prompt") or "").strip()
                if not prompt:
                    return self._send(400, {"error": "prompt is empty"})
                size = max(256, min(1024, int(req.get("size", 512)) // 16 * 16))
                steps = max(1, min(8, int(req.get("steps", 4))))
                data, ext, secs = gen.generate(prompt, size, steps, req.get("seed"), req.get("format", "webp"))
                self._send(200, {"image": base64.b64encode(data).decode(), "ext": ext, "seconds": round(secs, 2)})
            except Exception as e:  # report to the add-on instead of dropping the connection
                import traceback

                traceback.print_exc()
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def log_message(self, fmt, *args):
            pass

    return Handler


def pid_alive(pid):
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def exit_with_parent(pid):
    while True:
        time.sleep(5)
        if not pid_alive(pid):
            print("Anki is gone, exiting", flush=True)
            os._exit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8189)
    ap.add_argument("--idle-minutes", type=float, default=10, help="free the GPU after this long without requests (0: never)")
    ap.add_argument("--preload", action="store_true")
    ap.add_argument("--parent-pid", type=int, default=0, help="exit when this process (Anki) is gone")
    args = ap.parse_args()
    gen = Generator(args.idle_minutes)
    if args.parent_pid:
        threading.Thread(target=exit_with_parent, args=(args.parent_pid,), daemon=True).start()
    if args.preload:
        gen.pipe = load_pipeline()
    print(f"picture server on http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(gen)).serve_forever()


if __name__ == "__main__":
    main()

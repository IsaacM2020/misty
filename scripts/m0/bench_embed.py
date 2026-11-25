# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "mlx>=0.32.3",
#   "transformers>=5.18.0",
#   "mlx-vlm @ git+https://github.com/Blaizzy/mlx-vlm.git@3d87e88402f307efbf68e568971aa887ee7d9ed0",
#   "pillow", "numpy", "huggingface_hub", "torch", "torchvision",
# ]
# ///
"""M0 micro-benchmark: EmbeddingGemma 2 via MLX on this Mac. Prints numbers only, no file contents."""
import time, resource, sys, json, random
import numpy as np
import mlx.core as mx
from PIL import Image, ImageDraw

REPO = "mlx-community/embeddinggemma-2-bf16"
QP, DP = "task: search result | query: ", "title: none | text: "
out = {"repo": REPO}

def rss_mb():  # macOS ru_maxrss is bytes
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6

t0 = time.perf_counter()
from mlx_vlm.embedding_loader import load_embedding_model
from mlx_vlm.utils import get_model_path
from transformers import AutoTokenizer
path = get_model_path(REPO)
model = load_embedding_model(path)
tok = AutoTokenizer.from_pretrained(path)
out["load_s"] = round(time.perf_counter() - t0, 1)

def embed(texts, bs=16):
    vecs = []
    for i in range(0, len(texts), bs):
        enc = tok(texts[i:i + bs], padding=True, truncation=True, max_length=512, return_tensors="np")
        e = model(**{k: mx.array(v) for k, v in enc.items()}).text_embeds
        mx.eval(e)
        vecs.append(np.array(e.astype(mx.float32)))
    return np.concatenate(vecs)

# semantic sanity
docs = ["Kohli scored 82 not out, lifting his season batting average above 55 runs per dismissal.",
        "During electrolysis of brine, chlorine forms at the anode and hydrogen at the cathode.",
        "The iGEM team finished the wiki draft and will submit the safety form on Friday."]
q = embed([QP + "runs scored per dismissal in a season"])[0]
d = embed([DP + x for x in docs])
out["semantic_top"] = int(np.argmax(d @ q))          # expect 0
out["dim"] = int(d.shape[1]); out["norm_ok"] = bool(np.allclose(np.linalg.norm(d, axis=1), 1, atol=1e-2))

# throughput: 256 synthetic ~350-token paragraphs
random.seed(7)
vocab = ("project deadline wiki iGEM physics chemistry essay draft meeting email calendar reminder cricket batting "
         "bowling economics supply demand elasticity python function loop dictionary startup pitch college application "
         "essay MIT robotics sensor firmware network router backup folder photo trip family dinner plan").split()
paras = [" ".join(random.choice(vocab) for _ in range(260)) for _ in range(256)]
_ = embed([DP + paras[0]])  # warm
t = time.perf_counter(); v = embed([DP + p for p in paras]); dt = time.perf_counter() - t
out["docs_per_s"] = round(len(paras) / dt, 1)
out["avg_tokens"] = int(np.mean([len(tok(DP + p)["input_ids"]) for p in paras[:20]]))

# images
img_ok, imgs = None, []
try:
    from mlx_vlm import load as vlm_load
    vmodel, processor = vlm_load(REPO)
    def pic(color, shape):
        im = Image.new("RGB", (448, 448), "white"); dr = ImageDraw.Draw(im)
        box = (96, 96, 352, 352)
        dr.ellipse(box, fill=color) if shape == "circle" else dr.rectangle(box, fill=color)
        return im
    specs = [("red", "circle"), ("blue", "square"), ("green", "circle"), ("yellow", "square"),
             ("black", "circle"), ("orange", "square"), ("purple", "circle"), ("gray", "square")]
    imgs = [pic(c, s) for c, s in specs]
    t = time.perf_counter()
    iv = []
    for im in imgs:
        inp = processor(images=[im], return_tensors="np")
        e = vmodel(**{k: mx.array(x) for k, x in inp.items()}).text_embeds; mx.eval(e)
        iv.append(np.array(e.astype(mx.float32))[0])
    out["images_per_s"] = round(len(imgs) / (time.perf_counter() - t), 2)
    iv = np.stack(iv)
    tq = embed([QP + "a red circle on a white background", QP + "a blue square on a white background"])
    sims = tq @ iv.T
    out["img_match_red_circle"] = bool(np.argmax(sims[0]) == 0)
    out["img_match_blue_square"] = bool(np.argmax(sims[1]) == 1)
    img_ok = True
except Exception as ex:
    out["image_error"] = f"{type(ex).__name__}: {str(ex)[:200]}"; img_ok = False
out["image_ok"] = img_ok
out["peak_rss_mb"] = round(rss_mb())
try: out["mlx_peak_gpu_mb"] = round(mx.get_peak_memory() / 1e6)
except Exception: pass
print(json.dumps(out, indent=1))

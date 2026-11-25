import numpy as np
import pytest

from fakes import FakeEmbedder
from misty_engine.embed import GemmaEmbedder, load_embedder


def test_fake_embedder_shapes_and_norm():
    e = FakeEmbedder()
    v = e.embed_documents(["a b c", "d e f"])
    assert v.shape == (2, 768) and np.allclose(np.linalg.norm(v, axis=1), 1)
    assert e.embed_query("a b c").shape == (768,)


def test_fake_embedder_similar_strings_are_close():
    e = FakeEmbedder()
    q = e.embed_query("iGEM wiki deadline")
    d = e.embed_documents(["the iGEM wiki deadline is Friday", "electrolysis of brine at the anode"])
    assert float(d[0] @ q) > float(d[1] @ q)


def test_gemma_embedder_is_lazy(pinned):
    g = load_embedder(pinned)
    assert isinstance(g, GemmaEmbedder) and g.dim == pinned.embedding.dim
    assert not g.loaded          # constructing must not load a 1.5 GB model


@pytest.mark.live
def test_gemma_embedder_semantics(pinned, tmp_path):
    e = load_embedder(pinned)
    q = e.embed_query("cricket batting average")
    d = e.embed_documents(["runs scored per dismissal in a season", "electrolysis of brine at the anode"])
    assert float(d[0] @ q) > float(d[1] @ q)
    assert d.shape[1] == pinned.embedding.dim and np.allclose(np.linalg.norm(d, axis=1), 1, atol=1e-3)
    assert e.embed_documents([]).shape == (0, pinned.embedding.dim)
    # order is preserved even though batches are length-sorted internally
    texts = ["short", "a much longer text " * 30, "mid length text here"]
    one_by_one = np.stack([e.embed_documents([t])[0] for t in texts])
    assert np.allclose(e.embed_documents(texts), one_by_one, atol=2e-2)


@pytest.mark.live
def test_gemma_embedder_images(pinned, tmp_path):
    from PIL import Image, ImageDraw
    e = load_embedder(pinned)
    paths = []
    for name, color, shape in (("red_circle.png", "red", "circle"), ("blue_square.png", "blue", "square")):
        im = Image.new("RGB", (448, 448), "white")
        box = (96, 96, 352, 352)
        d = ImageDraw.Draw(im)
        d.ellipse(box, fill=color) if shape == "circle" else d.rectangle(box, fill=color)
        p = tmp_path / name
        im.save(p)
        paths.append(p)
    iv = e.embed_images(paths)
    assert iv.shape == (2, pinned.embedding.dim)
    q = e.embed_query("a red circle on a white background")
    assert int(np.argmax(iv @ q)) == 0


@pytest.mark.live
def test_gemma_embedder_drops_unused_towers_without_changing_text_vectors(pinned, tmp_path):
    import mlx.core as mx
    from PIL import Image
    before = mx.get_active_memory()                                 # earlier tests' models may still be around
    e = load_embedder(pinned)
    first = e.embed_query("what is on my calendar today")
    assert e._model["audio_tower"] is None and e._model["vision_tower"] is None
    assert mx.get_active_memory() - before < 800 * 1024 * 1024            # was 1.5 GB with every tower
    p = tmp_path / "x.png"
    Image.new("RGB", (448, 448), "red").save(p)
    assert e.embed_images([p]).shape == (1, pinned.embedding.dim)  # photos still work: the tower comes back
    assert e._model["vision_tower"] is not None and e._model["audio_tower"] is None
    assert np.array_equal(e.embed_query("what is on my calendar today"), first)
    e._last_image -= 10_000                                         # photo tower idle for a long time: dropped again
    e.embed_query("anything")
    assert e._model["vision_tower"] is None
    assert np.array_equal(e.embed_query("what is on my calendar today"), first)


@pytest.mark.live
def test_gemma_embedder_hands_back_scratch_memory_when_idle(pinned, monkeypatch):
    import time
    import mlx.core as mx
    from misty_engine import embed
    monkeypatch.setattr(embed, "IDLE_CLEAR_SECONDS", 0.3)
    e = load_embedder(pinned)
    e.embed_documents(["a long enough text to make some scratch buffers " * 20] * 16)
    time.sleep(1.5)
    assert mx.get_cache_memory() == 0

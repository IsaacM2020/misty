"""A fake model for the free dry runs of the F checks: no network, no money. Not a check itself."""
from __future__ import annotations


class FakeCachingBrain:
    """Behaves like the provider's cache: the first call with a given system text is a miss, the next ones hit."""
    def __init__(self):
        self.seen: set[str] = set()

    async def post(self, path, body, *, on_delta=None, **kw):
        sys_msg = body["messages"][0]["content"]
        key = sys_msg[0]["text"] if isinstance(sys_msg, list) else str(sys_msg)
        hit = key in self.seen
        self.seen.add(key)
        text = "" if body.get("max_tokens") == 1 else "Sure. Here you go."
        if on_delta and text:
            await on_delta(text)
        return {"choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": {"cost": 0.0004, "prompt_tokens": 11900, "completion_tokens": 5 if text else 1,
                          "prompt_tokens_details": {"cached_tokens": 11000 if hit else 0}}}

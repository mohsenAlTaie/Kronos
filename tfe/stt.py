from __future__ import annotations

import asyncio
import io
import re
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, Tuple

from .ai.router import classify_error, parse_retry_after
from .config import Settings
from .logs import AI as log

_HALLUCINATIONS = {
    "شكرا للمشاهدة", "شكرا لكم على المشاهدة", "اشتركوا في القناة", "ترجمة نانسي قنقر", "thank you", "thanks for watching",
    "thank you for watching", "you", "bye", "subtitles by the amara.org community",
}
_EXT = {"audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
        "audio/wav": "wav", "audio/webm": "webm", "audio/flac": "flac"}


class Transcriber:
    def __init__(self, settings: Settings, *, fn: Optional[Callable[..., Awaitable[Any]]] = None,
                 clock: Callable[[], float] = time.monotonic, per_minute: int = 5):
        self.s = settings
        self._fn = fn
        self._clock = clock
        self.per_minute = per_minute
        self._hour: Deque[Tuple[float, int]] = deque()
        self._users: Dict[int, Deque[float]] = {}
        self._down_until: Dict[str, float] = {}
        self.stats: Dict[str, int] = {"ok": 0, "failed": 0, "skipped": 0, "empty": 0}

    def _user_ok(self, user_id: int) -> bool:
        now = self._clock()
        q = self._users.setdefault(user_id, deque(maxlen=32))
        while q and q[0] < now - 60.0:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        if len(self._users) > 2000:
            self._users.pop(next(iter(self._users)))
        return True

    def _budget_ok(self, secs: int) -> bool:
        now = self._clock()
        while self._hour and self._hour[0][0] < now - 3600.0:
            self._hour.popleft()
        return sum(b for _, b in self._hour) + secs <= self.s.stt_hourly_seconds

    async def transcribe(self, data: bytes, mime: str, seconds: int, user_id: int) -> Optional[str]:
        if not data or seconds > self.s.voice_max_seconds or len(data) > self.s.voice_max_bytes:
            self.stats["skipped"] += 1
            return None
        billed = max(10, int(seconds or 0))
        if not self._user_ok(user_id) or not self._budget_ok(billed):
            self.stats["skipped"] += 1
            log.info("voice note skipped (rate / hourly audio budget)")
            return None
        models = [m for m in (self.s.stt_model, self.s.stt_fallback_model) if m]
        for model in dict.fromkeys(models):
            if self._down_until.get(model, 0.0) > self._clock():
                continue
            try:
                text = await asyncio.wait_for(self._call(model, data, mime), timeout=self.s.stt_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                kind = classify_error(e)
                cool = (parse_retry_after(str(e)) or 20.0) + 0.5 if kind == "rate_limit" else (3600.0 if kind == "fatal" else 15.0)
                self._down_until[model] = self._clock() + min(cool, 3600.0)
                self.stats["failed"] += 1
                log.warning(f"speech-to-text {model} failed ({kind}: {type(e).__name__}) — paused {min(cool, 3600.0):.0f}s")
                continue
            self._hour.append((self._clock(), billed))
            clean = self._clean(text)
            if not clean:
                self.stats["empty"] += 1
                return None
            self.stats["ok"] += 1
            return clean
        return None

    async def _call(self, model: str, data: bytes, mime: str) -> str:
        fn = self._fn
        if fn is None:
            import litellm
            fn = litellm.atranscription
        buf = io.BytesIO(data)
        buf.name = f"voice.{_EXT.get((mime or '').lower(), 'ogg')}"
        kwargs: Dict[str, Any] = {"model": model, "file": buf}
        if self.s.stt_language:
            kwargs["language"] = self.s.stt_language
        resp = await fn(**kwargs)
        return resp if isinstance(resp, str) else (getattr(resp, "text", "") or (resp.get("text", "") if isinstance(resp, dict) else ""))

    @staticmethod
    def _clean(text: str) -> str:
        t = re.sub(r"\s+", " ", text or "").strip()
        norm = re.sub(r"[^\w\s]", "", t).strip().lower()
        if not norm or norm in _HALLUCINATIONS or len(norm) < 2:
            return ""
        return t[:1500]

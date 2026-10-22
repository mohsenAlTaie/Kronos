from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .config import Settings
from .logs import SEARCH as log
from .moderation.normalizer import base_normalize
from .utils import TTLCache, shorten

_SEARCH_INTENT = re.compile(
    r"(ابحث|دور لي|دورلي|بحث عن|search for|look up|google it|"
    r"اخر\s*(?:اخبار|خبر|نسخه|اصدار|تحديث)|اخبار|خبر\s*جديد|breaking|latest|news|"
    r"سعر|اسعار|بكم|كم سعر|price of|how much is|exchange rate|سعر الصرف|سعر الدولار|"
    r"طقس اليوم|درجه الحراره|weather|forecast|"
    r"نتيجه مباراه|نتيجه ماتش|مباراه اليوم|ماتش اليوم|score of|"
    r"من فاز|who won|من رئيس|who is the (?:current )?(?:president|ceo|prime)|"
    r"الى الان|حاليا|هسه شكد|right now|currently|as of today|today's|"
    r"release date|موعد صدور|متى ينزل|متى يصدر|اخر اصدار|latest version|"
    r"202[5-9])", re.I)
_NO_SEARCH = re.compile(r"^(هلا|مرحبا|شلونك|السلام|hi|hello|hey|thanks|شكرا|تمام|اوكي|ok)\b", re.I)


def needs_search(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 6 or _NO_SEARCH.match(t):
        return False
    return bool(_SEARCH_INTENT.search(base_normalize(t).lower()) or _SEARCH_INTENT.search(t.lower()))


@dataclass
class SearchResult:
    ok: bool
    text: str = ""
    error: str = ""
    cached: bool = False


class WebSearch:
    def __init__(self, settings: Settings, backend: Optional[Callable[..., Any]] = None):
        self.s = settings
        self._backend = backend
        self._cache = TTLCache(maxsize=200, ttl=settings.search_cache_ttl)
        self.provider = settings.web_search_provider
        self.enabled = settings.enable_auto_search
        self.failures = 0

    async def search(self, query: str, *, timeout: Optional[float] = None) -> SearchResult:
        q = re.sub(r"\s+", " ", (query or "").strip())[:200]
        if not q:
            return SearchResult(False, error="empty")
        key = (self.provider, q.lower())
        if self.s.search_cache_ttl > 0:
            hit = self._cache.get(key)
            if hit is not None:
                return SearchResult(True, hit, cached=True)
        try:
            items = await asyncio.wait_for(
                asyncio.to_thread(self._run, q), timeout=timeout or self.s.search_timeout)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.failures += 1
            log.warning("web search timed out")
            return SearchResult(False, error="timeout")
        except Exception as e:
            self.failures += 1
            log.warning(f"web search failed ({type(e).__name__})")
            return SearchResult(False, error=type(e).__name__)
        if not items:
            return SearchResult(False, error="no_results")
        text = self.format(items)
        if self.s.search_cache_ttl > 0:
            self._cache.set(key, text)
        self.failures = 0
        return SearchResult(True, text)

    def _run(self, q: str) -> list:
        if self._backend is not None:
            return list(self._backend(q, self.provider, self.s.web_search_max_results) or [])
        import litellm
        resp = litellm.search(query=q, search_provider=self.provider, max_results=self.s.web_search_max_results)
        out = []
        for r in getattr(resp, "results", None) or []:
            out.append({"title": getattr(r, "title", "") or "",
                        "snippet": getattr(r, "snippet", None) or getattr(r, "description", "") or "",
                        "url": getattr(r, "url", "") or ""})
        return out

    @staticmethod
    def format(items: list, max_chars: int = 1400) -> str:
        lines = []
        for i, it in enumerate(items[:6], 1):
            title = shorten(str(it.get("title", "")), 100)
            snip = shorten(str(it.get("snippet", "")), 240)
            host = re.sub(r"^https?://(www\.)?", "", str(it.get("url", ""))).split("/")[0]
            lines.append(f"{i}. {title} — {snip} [{host}]")
        return shorten("\n".join(lines), max_chars)

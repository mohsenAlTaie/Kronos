from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from telethon import errors, functions, types

from ..config import Settings
from ..logs import TELEGRAM as log

_SKIN = re.compile("[\U0001F3FB-\U0001F3FF\uFE0E\uFE0F\u200D]")
_FAMILIES = [
    "😂🤣😆😹😅😄😁😀😃😝😜🤪😛",
    "😍🥰😘😻😚😙💕💖💗💘💝❤🧡💛💚💙💜🖤🤍🤎♥💓💞",
    "😭😢😞😔😿🥺😩😫😥😓🙁☹",
    "😡😠🤬😤👿💢",
    "😮😲😱🤯😧😦😯",
    "😎😏😉😋😌🤓",
    "👍👌🤙✌🙌👏🤝💪",
    "🙏🤲🥹",
    "🤔🧐🙄😒😑😐",
    "😴😪🥱",
    "🔥💥⚡",
    "🥳🎉🎊🎂🎁",
    "😈👿💀☠👻",
    "🤗🫂",
    "🙈🙉🙊😳🫣",
    "😘😗😙😚💋",
]


def norm_emoji(e: str) -> str:
    return _SKIN.sub("", e or "").strip()


def _family_of(key: str) -> str:
    for fam in _FAMILIES:
        if key and key in fam:
            return fam
    return ""


class _Index:

    POOLS = ("faved", "recent", "sets")

    def __init__(self) -> None:
        self.by_emoji: Dict[str, Dict[str, List[Any]]] = {p: {} for p in self.POOLS}
        self.docs: Dict[str, List[Any]] = {p: [] for p in self.POOLS}
        self._seen: Dict[str, set] = {p: set() for p in self.POOLS}
        self.sets_total = 0
        self.sets_loaded = 0
        self.loaded_at = 0.0
        self.partial = False

    def add(self, pool: str, docs: Any, packs: Any = None) -> None:
        docs = list(docs or [])
        by_id = {getattr(d, "id", None): d for d in docs}
        mapped: set = set()
        for pack in packs or []:
            key = norm_emoji(getattr(pack, "emoticon", ""))
            for did in getattr(pack, "documents", None) or []:
                d = by_id.get(did)
                if d is not None and key:
                    self._put(pool, key, d)
                    mapped.add(did)
        for d in docs:
            if getattr(d, "id", None) in mapped:
                continue
            for attr in getattr(d, "attributes", None) or []:
                alt = norm_emoji(getattr(attr, "alt", "") or "")
                if alt:
                    self._put(pool, alt, d)
        for d in docs:
            self._remember(pool, d)

    def _remember(self, pool: str, d: Any) -> None:
        did = getattr(d, "id", None)
        if did not in self._seen[pool]:
            self._seen[pool].add(did)
            self.docs[pool].append(d)

    def _put(self, pool: str, key: str, d: Any) -> None:
        lst = self.by_emoji[pool].setdefault(key, [])
        if all(getattr(x, "id", None) != getattr(d, "id", None) for x in lst):
            lst.append(d)

    def total(self) -> int:
        return len({getattr(d, "id", None) for p in self.POOLS for d in self.docs[p]})


class StickerLibrary:
    def __init__(self, client: Any, settings: Settings, *, clock: Callable[[], float] = time.monotonic,
                 rng: Optional[random.Random] = None):
        self.client = client
        self.s = settings
        self._clock = clock
        self._rng = rng or random.Random()
        self._index: Optional[_Index] = None
        self._task: Optional[asyncio.Task] = None
        self._failed_at = -1e9
        self._last: Dict[int, Any] = {}
        self._search_cache: Dict[str, Tuple[float, list]] = {}

    async def _rpc(self, request: Any, timeout: float = 10.0) -> Any:
        try:
            return await asyncio.wait_for(self.client(request), timeout=timeout)
        except errors.FloodWaitError as e:
            wait = float(getattr(e, "seconds", 0) or 0)
            if wait > 5:
                raise
            await asyncio.sleep(wait + 0.2)
            return await asyncio.wait_for(self.client(request), timeout=timeout)

    async def _load(self) -> _Index:
        idx = _Index()
        for pool, req in (("faved", functions.messages.GetFavedStickersRequest(hash=0)),
                          ("recent", functions.messages.GetRecentStickersRequest(hash=0))):
            try:
                res = await self._rpc(req)
                idx.add(pool, getattr(res, "stickers", None), getattr(res, "packs", None))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug(f"stickers: {pool} not loaded ({type(e).__name__})")
        try:
            allst = await self._rpc(functions.messages.GetAllStickersRequest(hash=0))
            sets = [x for x in (getattr(allst, "sets", None) or [])
                    if not getattr(x, "archived", False) and not getattr(x, "masks", False)
                    and not getattr(x, "emojis", False)]
            sets.sort(key=lambda x: getattr(x, "installed_date", 0) or 0, reverse=True)
            idx.sets_total = len(sets)
            for st in sets[: self.s.sticker_max_sets]:
                try:
                    full = await self._rpc(functions.messages.GetStickerSetRequest(
                        stickerset=types.InputStickerSetID(id=st.id, access_hash=st.access_hash), hash=0))
                except asyncio.CancelledError:
                    raise
                except errors.FloodWaitError:
                    idx.partial = True
                    log.warning("stickers: FloodWait while loading sets — using what was loaded")
                    break
                except Exception:
                    continue
                idx.add("sets", getattr(full, "documents", None), getattr(full, "packs", None))
                idx.sets_loaded += 1
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            idx.partial = True
            log.debug(f"stickers: sets not loaded ({type(e).__name__})")
        idx.loaded_at = self._clock()
        log.info(f"stickers: {idx.total()} of YOUR stickers indexed "
                 f"({idx.sets_loaded}/{idx.sets_total} sets, {len(idx.docs['faved'])} favourites, "
                 f"{len(idx.docs['recent'])} recent)")
        return idx

    def _fresh(self) -> bool:
        return (self._index is not None
                and self._clock() - self._index.loaded_at < max(self.s.sticker_cache_hours, 0.01) * 3600.0)

    def warm(self) -> None:
        if self.s.sticker_source == "search" or self._fresh() or (self._task and not self._task.done()):
            return
        if self._clock() - self._failed_at < 300.0:
            return
        try:
            self._task = asyncio.get_running_loop().create_task(self._run_load(), name="tfe-stickers")
        except RuntimeError:
            self._task = None

    async def _run_load(self) -> None:
        try:
            idx = await self._load()
            if idx.total() or self._index is None:
                self._index = idx
            if not idx.total():
                self._failed_at = self._clock()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._failed_at = self._clock()
            log.warning(f"stickers: loading failed ({type(e).__name__})")

    async def ensure(self, wait: float = 12.0) -> Optional[_Index]:
        if not self._fresh():
            self.warm()
        if self._task is not None and not self._task.done() and self._index is None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=wait)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        return self._index

    async def refresh(self) -> Optional[_Index]:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._failed_at = -1e9
        self._index = None
        self._task = None
        await self._run_load()
        return self._index

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def stats(self) -> Dict[str, Any]:
        i = self._index
        if i is None:
            return {"loaded": False, "source": self.s.sticker_source}
        return {"loaded": True, "source": self.s.sticker_source, "stickers": i.total(), "sets_loaded": i.sets_loaded,
                "sets_total": i.sets_total, "favourites": len(i.docs["faved"]), "recent": len(i.docs["recent"]),
                "partial": i.partial, "age_minutes": (self._clock() - i.loaded_at) / 60.0}

    def top_emojis(self, n: int = 30) -> List[str]:
        i = self._index
        if i is None:
            return []
        score: Dict[str, float] = {}
        for pool, w in (("faved", 3.0), ("recent", 2.0), ("sets", 1.0)):
            for key, docs in i.by_emoji[pool].items():
                score[key] = score.get(key, 0.0) + w * len(docs)
        return [e for e, _ in sorted(score.items(), key=lambda kv: -kv[1])][:n]

    def _matches(self, idx: _Index, emoji: str) -> List[Any]:
        key = norm_emoji(emoji)
        for keys in ([key], list(_family_of(key))):
            keys = [norm_emoji(k) for k in keys if k]
            hot = [d for pool in ("faved", "recent") for k in keys for d in idx.by_emoji[pool].get(k, [])]
            if hot:
                return self._unique(hot)
            cold = [d for k in keys for d in idx.by_emoji["sets"].get(k, [])]
            if cold:
                return self._unique(cold)
        return []

    @staticmethod
    def _unique(docs: List[Any]) -> List[Any]:
        seen, out = set(), []
        for d in docs:
            did = getattr(d, "id", None)
            if did not in seen:
                seen.add(did)
                out.append(d)
        return out

    @staticmethod
    def _go_to(idx: _Index) -> List[Any]:
        hot = StickerLibrary._unique(idx.docs["faved"] + idx.docs["recent"])
        return hot[:40] if hot else idx.docs["sets"][:200]

    async def _search(self, emoji: str) -> List[Any]:
        key = norm_emoji(emoji)
        hit = self._search_cache.get(key)
        if hit is not None and self._clock() - hit[0] < 3600.0:
            return hit[1]
        try:
            res = await self._rpc(functions.messages.GetStickersRequest(emoticon=emoji, hash=0), timeout=6.0)
            docs = list(getattr(res, "stickers", None) or [])[:12]
        except asyncio.CancelledError:
            raise
        except Exception:
            docs = []
        self._search_cache[key] = (self._clock(), docs)
        return docs

    async def pick(self, emoji: str, chat_id: Optional[int] = None) -> Optional[Any]:
        mode = self.s.sticker_source
        idx = await self.ensure() if mode != "search" else None
        mine = self._matches(idx, emoji) if idx is not None else []
        if mode == "search":
            cands = await self._search(emoji)
        elif mine:
            cands = mine
        elif mode == "any":
            cands = await self._search(emoji) or (self._go_to(idx) if idx is not None else [])
        else:
            go_to = self._go_to(idx) if idx is not None else []
            cands = go_to or await self._search(emoji)
        if not cands:
            return None
        last = self._last.get(chat_id) if chat_id is not None else None
        pool = [d for d in cands if getattr(d, "id", None) != last] or cands
        doc = self._rng.choice(pool)
        if chat_id is not None:
            self._last[chat_id] = getattr(doc, "id", None)
            if len(self._last) > 2000:
                self._last.pop(next(iter(self._last)))
        return doc

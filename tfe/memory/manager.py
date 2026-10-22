from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from ..ai.prompts import FACT_EXTRACT_SYSTEM, SUMMARY_SYSTEM
from ..ai.roles import Role
from ..config import Settings
from ..logs import MEMORY as log
from ..moderation.normalizer import base_normalize
from ..utils import estimate_tokens, sanitize_user_text, shorten
from .db import Database

_TOK = re.compile(r"[^\W_]{2,}", re.U)
_STOP = {"في", "من", "على", "الى", "عن", "ما", "هذا", "هاي", "انت", "انا", "شنو", "the", "and", "you", "are", "for",
         "that", "this", "with", "have", "لا", "هو", "هي", "كل", "او", "مع", "يا"}


def _terms(text: str) -> Set[str]:
    t = base_normalize(text or "").lower()
    return {w for w in _TOK.findall(t) if w not in _STOP}


@dataclass
class MemoryContext:
    history: List[Dict[str, str]]
    facts: List[str]
    summary: str
    profile: Optional[Dict[str, Any]] = None
    last_ts: float = 0.0


class MemoryManager:
    def __init__(self, db: Database, settings: Settings, router: Any = None):
        self.db, self.s, self.router = db, settings, router
        self._summarizing: Set[int] = set()
        self._extract_counter: Dict[int, int] = {}
        self._touched: Dict[int, float] = {}

    async def load(self, user_id: int, query: str) -> MemoryContext:
        rows, facts, (summary, covered), profile = await asyncio.gather(
            self.db.recent_messages(user_id, self.s.max_context_messages * 2, self.s.memory_ttl_days),
            self.db.get_facts(user_id),
            self.db.get_summary(user_id),
            self.db.get_profile(user_id),
        )
        last_ts = float(rows[-1].get("ts") or 0) if rows else 0.0
        return MemoryContext(self._fit(rows), await self._relevant(facts, query), summary, profile, last_ts)

    async def touch_person(self, user_id: int, name: str = "", username: str = "", lang: str = "") -> None:
        import time as _t
        now = _t.monotonic()
        last = self._touched.get(user_id)
        pending = self._pending_count = getattr(self, "_pending_count", {})
        pending[user_id] = pending.get(user_id, 0) + 1
        if last is not None and now - last < self.s.profile_touch_seconds:
            return
        self._touched[user_id] = now
        if len(self._touched) > 5000:
            self._touched.clear()
        n, pending[user_id] = pending[user_id], 0
        first, _, last_name = (name or "").strip().partition(" ")
        try:
            await self.db.touch_profile(user_id, first, last_name.strip(), username or "", lang or "", n)
        except Exception as e:
            log.warning(f"profile update failed ({type(e).__name__})")

    def forget_runtime(self, user_id: int) -> None:
        self._touched.pop(user_id, None)
        self._extract_counter.pop(user_id, None)
        getattr(self, "_pending_count", {}).pop(user_id, None)
        self._summarizing.discard(user_id)

    async def archive_expired(self, limit_people: Optional[int] = None) -> int:
        if self.router is None or not self.s.enable_summarization:
            return 0
        done = 0
        for uid in await self.db.users_with_expired_messages(self.s.memory_ttl_days, limit_people or self.s.archive_batch):
            if uid in self._summarizing or await self.db.is_blocked(uid):
                continue
            self._summarizing.add(uid)
            try:
                summary, covered = await self.db.get_summary(uid)
                rows = await self.db.expired_messages(uid, self.s.memory_ttl_days, covered, 80)
                if len(rows) < 3:
                    continue
                chat = "\n".join(f"{'U' if r['role'] == 'user' else 'A'}: {r['content'][:250]}" for r in rows)
                prompt = f"Current summary:\n{summary or '(none)'}\n\nNew messages:\n<<<CHAT\n{chat}\nCHAT>>>"
                comp = await self.router.complete(Role.SUMMARY, [
                    {"role": "system", "content": SUMMARY_SYSTEM.format(n=self.s.summary_max_chars)},
                    {"role": "user", "content": prompt}],
                    max_tokens=260, temperature=0.2, timeout=self.s.summary_timeout,
                    deadline=self.s.summary_timeout + 5, max_models=2, max_retries=0)
                new = shorten(comp.text.strip(), self.s.summary_max_chars)
                if len(new) >= 10:
                    await self.db.set_summary(uid, new, rows[-1]["id"])
                    done += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"archiving old chat failed ({type(e).__name__}) — kept until hard TTL")
            finally:
                self._summarizing.discard(uid)
        return done

    def _fit(self, rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        rows = rows[-self.s.max_context_messages:]
        out: List[Dict[str, str]] = []
        used = 0
        for r in reversed(rows):
            c = (r["content"] or "")[:700]
            cost = estimate_tokens(c) + 4
            if used + cost > self.s.context_token_budget and out:
                break
            out.append({"role": r["role"], "content": c})
            used += cost
        out.reverse()
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    async def _relevant(self, facts: List[Dict[str, Any]], query: str) -> List[str]:
        if not facts or self.s.memory_top_k <= 0:
            return []
        q = _terms(query)
        scored = []
        for i, f in enumerate(facts):
            ft = _terms(f["fact"])
            overlap = len(q & ft)
            score = overlap * 3 + min(f.get("hits", 0), 5) * 0.1 + (0.5 if i < 2 else 0)
            scored.append((score, overlap, f))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [f for score, overlap, f in scored if overlap > 0][: self.s.memory_top_k]
        if not picked:
            picked = [f for _, _, f in scored[:1]]
        await self.db.bump_facts([f["id"] for f in picked])
        out, used = [], 0
        for f in picked:
            if used + len(f["fact"]) > self.s.memory_context_chars:
                break
            out.append(f["fact"])
            used += len(f["fact"])
        return out

    async def remember_turn(self, user_id: int, user_text: str, reply: Optional[str]) -> None:
        if await self.db.is_blocked(user_id):
            return
        if user_text:
            await self.db.add_message(user_id, "user", sanitize_user_text(user_text, self.s.max_input_chars))
        if reply:
            await self.db.add_message(user_id, "assistant", reply[: self.s.max_reply_chars])

    async def remember_user_only(self, user_id: int, user_text: str) -> None:
        if user_text and not await self.db.is_blocked(user_id):
            await self.db.add_message(user_id, "user", sanitize_user_text(user_text, self.s.max_input_chars))

    async def maybe_maintain(self, user_id: int, every: int = 4) -> None:
        try:
            if self.router is None or not self.s.enable_summarization or await self.db.is_blocked(user_id):
                return
            n = self._extract_counter.get(user_id, 0) + 1
            self._extract_counter[user_id] = n
            if len(self._extract_counter) > 2000:
                self._extract_counter.clear()
            if n % max(1, every) == 0:
                await self._extract_facts(user_id)
            count = await self.db.message_count(user_id)
            if count >= self.s.max_context_messages * 2 + 6:
                await self.summarize(user_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"memory maintenance failed ({type(e).__name__})")

    async def _extract_facts(self, user_id: int) -> int:
        rows = await self.db.recent_messages(user_id, 10)
        if not rows:
            return 0
        chat = "\n".join(f"{'U' if r['role'] == 'user' else 'A'}: {r['content'][:300]}" for r in rows)
        try:
            comp = await self.router.complete(Role.SUMMARY, [
                {"role": "system", "content": FACT_EXTRACT_SYSTEM},
                {"role": "user", "content": f"<<<CHAT\n{chat}\nCHAT>>>"}],
                max_tokens=200, temperature=0.0, timeout=self.s.summary_timeout, deadline=self.s.summary_timeout + 5,
                max_models=2, max_retries=0)
        except Exception:
            return 0
        added = 0
        for fact in self.parse_facts(comp.text):
            if await self.db.add_fact(user_id, fact, self.s.fact_ttl_days):
                added += 1
        return added

    @staticmethod
    def parse_facts(text: str) -> List[str]:
        m = re.search(r"\[.*?\]", text or "", re.S)
        if not m:
            return []
        try:
            arr = json.loads(m.group(0))
        except ValueError:
            return []
        out = []
        for x in arr if isinstance(arr, list) else []:
            if isinstance(x, str) and 4 <= len(x.strip()) <= 140 and not re.search(r"(password|كلمة السر|otp|\b\d{9,}\b)", x, re.I):
                out.append(x.strip())
        return out[:5]

    async def summarize(self, user_id: int) -> bool:
        if user_id in self._summarizing or self.router is None:
            return False
        self._summarizing.add(user_id)
        try:
            summary, covered = await self.db.get_summary(user_id)
            keep = self.s.max_context_messages
            rows = await self.db.messages_after(user_id, covered, 80)
            total = await self.db.message_count(user_id)
            if len(rows) <= keep:
                return False
            old = rows[:-keep]
            chat = "\n".join(f"{'U' if r['role'] == 'user' else 'A'}: {r['content'][:250]}" for r in old)
            prompt = f"Current summary:\n{summary or '(none)'}\n\nNew messages:\n<<<CHAT\n{chat}\nCHAT>>>"
            comp = await self.router.complete(Role.SUMMARY, [
                {"role": "system", "content": SUMMARY_SYSTEM.format(n=self.s.summary_max_chars)},
                {"role": "user", "content": prompt}],
                max_tokens=260, temperature=0.2, timeout=self.s.summary_timeout, deadline=self.s.summary_timeout + 5,
                max_models=2, max_retries=0)
            new = shorten(comp.text.strip(), self.s.summary_max_chars)
            if len(new) < 10:
                return False
            await self.db.set_summary(user_id, new, old[-1]["id"])
            await self.db.prune_messages(user_id, keep * 2)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"summarization failed ({type(e).__name__}) — history kept")
            return False
        finally:
            self._summarizing.discard(user_id)

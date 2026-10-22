from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from ..config import Settings
from ..logs import MOD as log
from ..memory.db import Database
from ..utils import LangHint
from . import rules
from .classifier import Classification, Moderator
from .image import ImageVerdict

WARNING_IRAQI = "⚠️ انتبه، هاي أول وآخر ملاحظة. خلي الكلام باحترام، وإذا تكررت الإساءة راح يصير حظر مباشر."
WARNING_EN = "⚠️ Heads up: this is your first and last warning. Keep it respectful, or you'll be blocked."
IMG_WARNING_IRAQI = "⚠️ انتبه، هاي أول وآخر ملاحظة. ما أقبل صور مخلة أو مسيئة، وإذا تكررت راح يصير حظر مباشر."
IMG_WARNING_EN = "⚠️ Heads up: this is your first and last warning. I don't accept explicit or offensive pictures, or you'll be blocked."
IMG_BLOCK_IRAQI = "⛔ أرسلت صور مخلة أو مسيئة، لذلك تم حظرك."
IMG_BLOCK_EN = "⛔ You sent explicit or offensive pictures, so you've been blocked."
BLOCK_IRAQI = "⛔ تكررت الإساءة بعد التحذير، لذلك تم حظرك."
BLOCK_EN = "⛔ The abuse continued after the warning, so you've been blocked."
SEVERE_BLOCK_IRAQI = "⛔ هذا النوع من الكلام ممنوع تماماً، لذلك تم حظرك مباشرة."
SEVERE_BLOCK_EN = "⛔ That kind of message is not tolerated, so you've been blocked right away."

_KIND_FOR_MEDIA = {"video": "video", "gif": "video", "video_note": "video"}


@dataclass
class Decision:
    action: str
    classification: Optional[Classification] = None
    message: str = ""
    count: int = 0
    kind: str = "text"
    evidence: str = ""
    instant: bool = False


def warning_text(lang: LangHint) -> str:
    return WARNING_EN if lang.dialect == "english" else WARNING_IRAQI


def block_text(lang: LangHint) -> str:
    return BLOCK_EN if lang.dialect == "english" else BLOCK_IRAQI


def severe_block_text(lang: LangHint) -> str:
    return SEVERE_BLOCK_EN if lang.dialect == "english" else SEVERE_BLOCK_IRAQI


_IMAGE_TO_LABEL = {"SEXUAL": "SEXUAL_ABUSE", "INSULT": "INSULT", "GORE": "HARASSMENT", "HATE": "HARASSMENT",
                   "SUGGESTIVE": "SEXUAL_ABUSE"}


def _clip(text: str, n: int = 300) -> str:
    return " ".join((text or "").split())[:n]


class ModerationService:
    def __init__(self, settings: Settings, db: Database, moderator: Moderator):
        self.s = settings
        self.db = db
        self.moderator = moderator
        self.enabled = settings.moderation_enabled
        self._blocked: Set[int] = set()
        self.stats = {"warnings": 0, "blocks": 0, "spam_ignored": 0, "instant_blocks": 0}
        self._trusted: Dict[int, Tuple[str, str]] = {}
        self._always: Dict[int, Tuple[str, str]] = {}
        if getattr(moderator, "learn", None) is None:
            moderator.learn = self._learn

    async def load(self) -> None:
        self._blocked = set(await self.db.blocked_ids())
        log.info(f"loaded {len(self._blocked)} permanent blocks")
        active = await self.db.active_terms()
        rules.set_learned(active)
        log.info(f"loaded {len(active)} learned abusive terms")
        self._trusted = {r["user_id"]: (r["tier"], r["label"]) for r in await self.db.trust_list()}
        log.info(f"loaded {len(self._trusted)} VIP people (the only ones never warned or blocked)")

    def set_always_trusted(self, user_id: int, tier: str = "love", label: str = "") -> None:
        self._always[int(user_id)] = (tier, label)
        self._blocked.discard(int(user_id))

    def is_trusted(self, user_id: int) -> bool:
        return user_id in self._trusted or user_id in self._always

    def trust_tier(self, user_id: int) -> str:
        return (self._trusted.get(user_id) or self._always.get(user_id) or ("", ""))[0]

    async def trust_add(self, user_id: int, tier: str = "vip", label: str = "") -> bool:
        was_blocked = user_id in self._blocked
        await self.db.trust_add(user_id, tier, label)
        self._trusted[user_id] = (tier, label)
        self._blocked.discard(user_id)
        await self.db.unblock(user_id)
        if was_blocked:
            await self.db.add_mod_event(user_id, "unblock", reason="added to the VIP list")
        return was_blocked

    async def trust_remove(self, user_id: int) -> bool:
        self._trusted.pop(user_id, None)
        return await self.db.trust_remove(user_id)

    async def trust_list(self):
        return await self.db.trust_list()

    async def _learn(self, term: str, label: str) -> None:
        hits, status = await self.db.learn_term(term, label, "llm", getattr(self.s, "learn_promote_hits", 2))
        if status == "active":
            rules.add_learned(term, label)
        log.info(f"learned term '{term}' ({label}) hits={hits} status={status}")

    async def add_term(self, term: str) -> bool:
        n = rules._np(term)
        if not n:
            return False
        await self.db.learn_term(n, "PROFANITY", "owner", 1)
        return rules.add_learned(n, "PROFANITY")

    async def reject_term(self, term: str) -> bool:
        n = rules._np(term)
        if not n:
            return False
        rules.remove_learned(n)
        return await self.db.set_term_status(n, "rejected")

    async def approve_term(self, term: str) -> bool:
        n = rules._np(term)
        if not n:
            return False
        ok = await self.db.set_term_status(n, "active")
        rules.add_learned(n, "PROFANITY")
        return ok

    async def list_terms(self):
        return await self.db.list_terms()

    def is_blocked(self, user_id: int) -> bool:
        return user_id in self._blocked

    async def block(self, user_id: int, reason: str = "manual") -> None:
        await self.trust_remove(user_id)
        await self.db.block(user_id, reason, "MANUAL")
        await self.db.add_mod_event(user_id, "manual_block", kind="manual", label="MANUAL", reason=reason,
                                    evidence="blocked by the owner")
        self._blocked.add(user_id)

    async def unblock(self, user_id: int) -> bool:
        self._blocked.discard(user_id)
        was = await self.db.unblock(user_id)
        if was:
            await self.db.add_mod_event(user_id, "unblock", reason="unblocked by the owner")
        return was

    async def forget(self, user_id: int) -> Dict[str, int]:
        self._blocked.discard(user_id)
        self._trusted.pop(user_id, None)
        return await self.db.forget_everything(user_id)

    async def clear_warning(self, user_id: int) -> bool:
        had = await self.db.clear_warning(user_id)
        if had:
            await self.db.add_mod_event(user_id, "unwarn", reason="warning cleared by the owner")
        return had

    async def person_card(self, user_id: int, events: int = 5) -> Dict[str, Any]:
        profile = await self.db.get_profile(user_id) or {}
        blocked = next((b for b in await self.db.blocked_page(0, 500) if b["user_id"] == user_id), None) \
            if user_id in self._blocked else None
        warn_count = await self.db.get_warning_count(user_id, self.s.warning_ttl_hours)
        return {"user_id": user_id, "profile": profile, "blocked": blocked, "warnings": warn_count,
                "vip": self._trusted.get(user_id) or self._always.get(user_id), "events": await self.db.mod_events_for(user_id, events),
                "overview": await self.db.user_overview(user_id)}

    def _instant_text(self, c: Classification) -> bool:
        return (c.label in set(self.s.instant_block_labels) and c.confidence >= self.s.instant_block_confidence)

    async def inspect(self, user_id: int, text: str, lang: LangHint, quote: str = "", kind: str = "text") -> Decision:
        if user_id in self._blocked:
            return Decision("ignore_blocked")
        if self.is_trusted(user_id):
            return Decision("pass")
        if not self.enabled or not text:
            return Decision("pass")
        strict = False
        if self.s.repeat_offender_strict:
            strict = await self.db.get_warning_count(user_id, self.s.warning_ttl_hours) > 0
        c = await self.moderator.evaluate(text, user_id, quote=quote, strict=strict)
        if c.label == "SPAM" and c.confidence >= self.s.moderation_confidence:
            self.stats["spam_ignored"] += 1
            return Decision("ignore_spam", c, kind=kind)
        if not c.actionable(self.s.moderation_confidence):
            return Decision("pass", c, kind=kind)

        instant = self._instant_text(c)
        evidence = f"«{_clip(text)}»"
        out = await self.db.register_abuse(
            user_id, max_warnings=self.s.max_warnings, ttl_hours=self.s.warning_ttl_hours,
            reason=f"{c.label}:{c.reason}", label=c.label, kind=kind, evidence=evidence, confidence=c.confidence,
            source=c.source, instant=instant)
        await self.db.log_moderation(user_id, c.label, c.confidence, c.source, out.action)
        if out.action == "already_blocked":
            self._blocked.add(user_id)
            return Decision("ignore_blocked", c, kind=kind)
        if out.action == "block":
            self._blocked.add(user_id)
            self.stats["blocks"] += 1
            sev = instant and out.count >= self.s.max_warnings and (out.count == self.s.max_warnings)
            self.stats["instant_blocks"] += 1 if instant else 0
            log.warning(f"user={user_id} classification={c.label} confidence={c.confidence:.2f} reason={c.reason}"
                        f" → PERMANENT BLOCK{' (severe, no warning)' if instant else ''}")
            msg = severe_block_text(lang) if instant else block_text(lang)
            return Decision("block", c, msg, out.count, kind=kind, evidence=evidence, instant=instant)
        self.stats["warnings"] += 1
        log.warning(f"user={user_id} classification={c.label} confidence={c.confidence:.2f} reason={c.reason} → WARNING #{out.count}")
        return Decision("warn", c, warning_text(lang), out.count, kind=kind, evidence=evidence)

    async def inspect_image(self, user_id: int, v: ImageVerdict, lang: LangHint, kind: str = "",
                            frames: Tuple[int, int] = (0, 0)) -> Decision:
        if user_id in self._blocked:
            return Decision("ignore_blocked")
        if self.is_trusted(user_id) or not self.enabled:
            return Decision("pass")
        if kind in ("sticker", "gif", "video") and v.label in ("INSULT", "GORE"):
            return Decision("pass")
        if v.label == "SPAM" and v.confidence >= max(0.85, self.s.moderation_confidence):
            self.stats["spam_ignored"] += 1
            return Decision("ignore_spam")
        if not v.actionable(self.s.moderation_confidence, self.s.image_strict):
            return Decision("pass")

        media = _KIND_FOR_MEDIA.get(kind, "image")
        c = Classification(_IMAGE_TO_LABEL[v.label], v.confidence, "vision", f"{media}:{v.label}:{v.kind}", llm_used=True)
        immediate = v.label == "SEXUAL" and (v.minor_risk or self.s.instant_block_nsfw)
        flags = ",".join(v.flags)
        evidence = _clip(f"{'🎞 ' if media == 'video' else '🖼 '}{v.label} ({flags or '-'}) {v.description}", 300)
        if frames[1] > 1:
            evidence += f" [frames {frames[0]}/{frames[1]}]"
        out = await self.db.register_abuse(
            user_id, max_warnings=1 if immediate else self.s.max_warnings, ttl_hours=self.s.warning_ttl_hours,
            reason=f"{media.upper()}:{v.label}:{v.kind}", label=c.label, kind=media, evidence=evidence,
            confidence=v.confidence, source="vision", instant=immediate)
        await self.db.log_moderation(user_id, c.label, v.confidence, "vision", out.action)
        english = lang.dialect == "english"
        if out.action == "already_blocked":
            self._blocked.add(user_id)
            return Decision("ignore_blocked", c, kind=media)
        if out.action == "block":
            self._blocked.add(user_id)
            self.stats["blocks"] += 1
            self.stats["instant_blocks"] += 1 if immediate else 0
            log.warning(f"user={user_id} {media}={v.label} confidence={v.confidence:.2f} minor_risk={v.minor_risk} → PERMANENT BLOCK")
            return Decision("block", c, IMG_BLOCK_EN if english else IMG_BLOCK_IRAQI, out.count, kind=media,
                            evidence=evidence, instant=immediate)
        self.stats["warnings"] += 1
        log.warning(f"user={user_id} {media}={v.label} confidence={v.confidence:.2f} → WARNING #{out.count}")
        return Decision("warn", c, IMG_WARNING_EN if english else IMG_WARNING_IRAQI, out.count, kind=media, evidence=evidence)

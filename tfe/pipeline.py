from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
import base64
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Set, Tuple

from .ai.prompts import IMAGE_FAILED_NOTE, PromptInputs, build_messages, failure_reply, image_note, media_reply
from .ai import relationship
from .ai.roles import Role
from .ai.router import AIModelRouter, AIUnavailable
from .config import Settings
from .logs import AI as ai_log, ERROR as err_log, TELEGRAM as tg_log
from .memory.manager import MemoryManager
from .moderation.image import ImageModerator, ImageVerdict
from .moderation.service import ModerationService
from .presence import PresenceManager
from .search import WebSearch, needs_search
from .stt import Transcriber
from .tgage import age_from_birthday, describe_account_age, estimate_creation, format_birthday_ar
from .utils import (SlidingWindowLimiter, TTLCache, clean_display_name, clean_markdown, detect_lang, detect_lang_ctx,
                    sanitize_user_text,
                    shorten, split_message, strip_foreign_cjk)


@dataclass
class Incoming:
    chat_id: int
    message_id: int
    user_id: int
    text: str = ""
    has_media: bool = False
    is_private: bool = True
    is_bot: bool = False
    is_mentioned: bool = False
    is_reply_to_me: bool = False
    date: float = field(default_factory=time.time)
    media_kind: str = ""
    sticker_emoji: str = ""
    image_loader: Optional[Callable[[], Awaitable[Optional[Tuple[bytes, str]]]]] = None
    thumb_loader: Optional[Callable[[], Awaitable[Optional[Tuple[bytes, str]]]]] = None
    sender_name: str = ""
    sender_username: str = ""
    is_contact: bool = False
    is_close_friend: bool = False
    voice_loader: Optional[Callable[[], Awaitable[Optional[Tuple[bytes, str, int]]]]] = None
    quote: str = ""
    quote_mine: bool = False
    frames_loader: Optional[Callable[[int], Awaitable[List[Tuple[bytes, str]]]]] = None
    edited: bool = False


@dataclass
class ImageRef:
    loader: Callable[[], Awaitable[Optional[Tuple[bytes, str]]]]
    verdict: Optional[ImageVerdict] = None
    kind: str = ""


IMAGE_TAG = "[صورة]"
VOICE_TAG = "[رسالة صوتية]"
_MEDIA_TAGS = {"photo": IMAGE_TAG, "image_doc": IMAGE_TAG, "sticker": "[ستيكر]", "gif": "[GIF]"}
_STICKER_WORD = r"(?:ستيكر|ستكر|ملصق|sticker)"
_EMOJI = r"[^\w\s\[\]()（）:：،,.!?؟]{1,8}"
_OPEN, _CLOSE = r"[\[(]{1,2}\s*", r"\s*[\])]{1,2}"
_STICKER_MARK = re.compile(_OPEN + _STICKER_WORD + r"\s*[:：]?\s*(" + _EMOJI + r")" + _CLOSE
                           + r"|" + _OPEN + r"(" + _EMOJI + r")\s*" + _STICKER_WORD + _CLOSE, re.I)
_BARE_STICKER = re.compile(_OPEN + _STICKER_WORD + _CLOSE, re.I)


def extract_sticker(text: str) -> Tuple[str, str]:
    t = text or ""
    found = ""
    for m in _STICKER_MARK.finditer(t):
        found = found or (m.group(1) or m.group(2) or "")
    t = _BARE_STICKER.sub("", _STICKER_MARK.sub("", t))
    return t.strip(), found


def canon_sticker(text: str) -> str:
    return _STICKER_MARK.sub(lambda m: f"[[ستيكر:{m.group(1) or m.group(2)}]]", text or "")


async def _no_bytes():
    return None


@dataclass
class _Held:
    chat_id: int
    message_id: int
    body: str
    ref: Optional["ImageRef"]
    ts: float

_TIER_LINES = {
    "vip": "من المميزين عند صاحب الحساب: احچي وياه باحترام وودّ.",
    "friend": "صديق لصاحب الحساب: احچي وياه بشكل ودي وعفوي كأنه صديق.",
    "love": "من الأحباب المقربين جداً لصاحب الحساب: كن دافئاً ولطيفاً وعفوياً وبدون رسميات (بدون مغازلة).",
    "close_friend": "من الأصدقاء المقربين لصاحب الحساب: احچي وياه بشكل ودي وعفوي.",
    "engaged": "صاحب الحساب نفسه يحجي وياه هذي الأيام: خلي الرد خفيف وودي ولا تتدخل بشكل زايد.",
}
_IMAGE_REF = re.compile(r"(صور[هة]|الصور[هة]|هاي|هذي|هذه|هذا|شوف|شفت\w*|رايك|رأيك|شرايك|عجبت\w*|حلو[ةه]|"
                        r"شنو بي|شنو ب|شنو فيه|شنو مكتوب|اقرا|اقرأ|وصف|ترجم|"
                        r"\bimage\b|\bphoto\b|\bpic(ture)?\b|\bthis\b|what'?s in|\bread\b|describe|translate)", re.I)


class Transport(Protocol):
    async def send(self, chat_id: int, text: str, reply_to: Optional[int] = None) -> bool: ...
    async def typing(self, chat_id: int, seconds: float) -> None: ...
    async def block(self, user_id: int) -> bool: ...
    async def unblock(self, user_id: int) -> bool: ...
    async def sender_info(self, user_id: int) -> Dict[str, Any]: ...
    async def notify_owner(self, text: str) -> None: ...
    async def delete_chat(self, chat_id: int, revoke: bool = True) -> bool: ...
    async def send_sticker(self, chat_id: int, emoji: str) -> bool: ...


_FILLER = re.compile(r"^\s*(بالتأكيد|بالطبع|بكل سرور|يسعدني|طبعاً|طبعا|Sure|Certainly|Of course|Absolutely)[\s,،:!.\-–]+", re.I)


def polish_reply(text: str, max_chars: int) -> str:
    t = clean_markdown(text or "")
    t = _FILLER.sub("", t, count=1).strip()
    t = re.sub(r"[✨👑🚀💎]+", "", t).strip()
    return shorten(t, max_chars)


_NAME_Q = re.compile(
    r"(شنو|شمو|ما|ماهو|ما هو|وش|ايش|إيش)\s*(هو\s*)?(اسمي|إسمي)|(اسمي|إسمي)\s*(شنو|شمو|وش|ايش)|تعرفني|تعرف\s*(شنو\s*)?(اسمي|إسمي|منو\s*(اني|انا|أني|أنا))|"
    r"(منو|مين|من)\s*(اني|انا|أني|أنا)\b|(اقرا|اقراء|اقرأ|شوف|شوفي)\s*(اسمي|إسمي)|"
    r"what('?s| is) my name|who am i|do you know (me|my name)|read my name", re.I)


def _name_note(raw_name: str, asked: bool) -> str:
    if not asked:
        return ""
    clean, readable = clean_display_name(raw_name)
    if readable:
        return (f"الشخص يسألك عن اسمه. اسمه على تيليجرام «{clean}». جاوبه بهذا الاسم مباشرة وبشكل طبيعي "
                f"(مثلاً: إي أعرفك، إنت {clean})، ولا تقل إنك ما تعرف اسمه.")
    return ("الشخص يسألك عن اسمه، بس اسمه على تيليجرام رموز أو زخرفة بدون حروف مقروءة. قل له هذا بصراحة وبخفة دم "
            "(ما كدرت أقراه)، واسأله شنو يحب تناديه.")


_BDAY_Q = re.compile(
    r"(شكد|شقد|كم|وشكد)\s*(هو\s*)?(عمري|سني)|عمري\s*(شكد|شقد|كم)|(مواليدي|ميلادي|مولدي)|عيد\s*(ميلادي|مولدي)|"
    r"(تاريخ|يوم)\s*(مواليدي|ميلادي|ولادتي)|متى\s*(ولدت|انولدت|اني\s*مولود)|تعرف\s*(عمري|مواليدي|ميلادي)|شنو\s*(مواليدي|ميلادي)|"
    r"my\s*(birthday|birth\s*date|age)\b|how old am i|when was i born|date of birth|do you know my (age|birthday)", re.I)
_ACCT_Q = re.compile(
    r"(تاريخ|وقت|يوم)\s*(انشاء|إنشاء|فتح|تسجيل|صنع|عمل)\s*(ال)?حساب|(ال)?حساب\w*\s*(انفتح|اتفتح|اتسجل|انسوى)|عمر\s*(ال)?حساب|"
    r"متى\s*(فتحت|سويت|سجلت|انشأت|أنشأت|دخلت\s*تيليجرام)|"
    r"account\s*(age|creation|created|date)|when (was|did) (my|i).*(account|join|creat)|how old is my account", re.I)


def _profile_note(info: Dict[str, Any], user_id: int, owner_id: int, owner_name: str, joined: str,
                  today: date) -> str:
    parts = []
    if _BDAY_Q.search(joined):
        b = info.get("birthday")
        if b:
            when = format_birthday_ar(b["day"], b["month"], b.get("year"))
            age = age_from_birthday(b["day"], b["month"], b.get("year"), today)
            parts.append(f"الشخص يسألك عن مواليده/عمره. مواليده المنشورة على ملفه بتيليجرام: {when}"
                         + (f" (عمره {age} سنة)" if age is not None else " (بدون السنة، فما تكدر تحسب عمره)")
                         + ". جاوبه بها مباشرة وبشكل طبيعي.")
        else:
            parts.append("الشخص يسألك عن مواليده/عمره، بس مواليده مو ظاهرة لك على تيليجرام (مو مضافة أو مخفية بخصوصيته). "
                         "قل له هذا بصراحة وبخفة دم، وإذا يريد خله يكتبها لك.")
    if _ACCT_Q.search(joined):
        est = estimate_creation(user_id, today)
        mine = (f"تاريخ إنشاء حسابه هو: {describe_account_age(est, today)}" if est else
                "ما تكدر تقدّر تاريخ إنشاء حسابه")
        theirs = estimate_creation(owner_id, today) if owner_id else None
        owner = (f" وإذا قصد حساب {owner_name} نفسه (صاحب الحساب) مو حسابه: {describe_account_age(theirs, today)}."
                 if theirs else "")
        parts.append(f"الشخص يسألك عن تاريخ إنشاء حساب. {mine}.{owner} قل له إنه تقدير تقريبي مو تاريخ دقيق (لأن "
                     "تيليجرام ما ينشره)، وجاوبه بشكل طبيعي وقصير.")
    return "\n".join(parts)


def _for_model(text: str) -> str:
    t = re.sub(r"\[ستيكر\]\s*", "(أرسل ستيكر) ", text or "")
    t = re.sub(r"\[GIF\]\s*", "(أرسل صورة متحركة GIF) ", t)
    t = re.sub(r"\[صورة\]\s*", "(أرسل صورة) ", t)
    return re.sub(r"\[رسالة صوتية\]\s*", "(أرسل رسالة صوتية، هذا نصها) ", t).strip()


def _first_emoji(text: str) -> str:
    for ch in text or "":
        if ord(ch) > 0x2000 and not ch.isalnum():
            return ch
    return ""


@dataclass
class _Burst:
    texts: List[str] = field(default_factory=list)
    first_msg_id: int = 0
    last_msg_id: int = 0
    chat_id: int = 0
    first_at: float = 0.0
    last_at: float = 0.0
    worker: Optional[asyncio.Task] = None
    images: List[ImageRef] = field(default_factory=list)


class MessagePipeline:
    def __init__(self, *, settings: Settings, transport: Transport, presence: PresenceManager,
                 moderation: ModerationService, memory: MemoryManager, router: AIModelRouter,
                 search: WebSearch, persona: Dict[str, Any], owner_id: int,
                 image_moderator: Optional[ImageModerator] = None, stt: Optional[Transcriber] = None):
        self.s = settings
        self.t = transport
        self.presence = presence
        self.mod = moderation
        self.mem = memory
        self.router = router
        self.search = search
        self.persona = persona
        self.owner_id = owner_id

        self.imod = image_moderator or ImageModerator(
            router, timeout=settings.image_moderation_timeout, tpm_budget=settings.vision_tpm_budget,
            max_wait=settings.image_moderation_max_wait, log_content=settings.log_message_content,
            confirm=settings.image_confirm)
        self.stt = stt or Transcriber(settings)
        self._held: Dict[int, List[_Held]] = {}
        self._sticker_for: Dict[int, str] = {}
        self._sticker_last = TTLCache(maxsize=2000, ttl=max(settings.sticker_cooldown, 0.001))
        presence.on_wake = self._on_wake
        self._engaged = TTLCache(maxsize=5000, ttl=max(settings.trust_engaged_hours, 0.0) * 3600.0 or 1.0)
        self.draft_mode = settings.draft_mode
        self.ai_reply_enabled = settings.ai_reply_enabled
        self._seen = TTLCache(maxsize=20000, ttl=settings.dedup_ttl)
        self._bursts: Dict[int, _Burst] = {}
        self._limiter = SlidingWindowLimiter(settings.rate_limit_per_minute, 60.0)
        self._sender_cache = TTLCache(maxsize=1000, ttl=3600.0)
        self._sender_seed = TTLCache(maxsize=2000, ttl=6 * 3600.0)
        self._last_image = TTLCache(maxsize=20, ttl=600.0)
        self._last_failure_reply: Dict[int, float] = {}
        self._last_global_failure_reply = -1e9
        self._bg: Set[asyncio.Task] = set()
        self._closing = False
        self.stats: Dict[str, int] = {
            "received": 0, "duplicates": 0, "blocked_ignored": 0, "ai_skipped_presence": 0, "ai_skipped_other": 0,
            "replies": 0, "drafts": 0, "discarded_presence": 0, "regenerated": 0, "ai_failures": 0,
            "moderation_warnings": 0, "moderation_blocks": 0, "coalesced_msgs": 0, "images_unchecked": 0,
            "trusted_skipped": 0, "voice_transcribed": 0, "voice_failed": 0, "held": 0, "held_answered": 0,
            "stickers_sent": 0,
        }

    async def handle(self, inc: Incoming) -> None:
        self.stats["received"] += 1
        if inc.user_id == self.owner_id or inc.is_bot or inc.user_id <= 0:
            return
        text = (inc.text or "").strip()
        if not text and inc.sticker_emoji:
            text = inc.sticker_emoji
        if not text and not inc.has_media:
            return
        if text.startswith("."):
            return
        if not inc.is_private and not (inc.is_mentioned or inc.is_reply_to_me):
            return
        if not self._seen.add_if_new((inc.chat_id, inc.message_id)):
            self.stats["duplicates"] += 1
            return

        if self.mod.is_blocked(inc.user_id):
            self.stats["blocked_ignored"] += 1
            return

        if inc.sender_name or inc.sender_username or inc.is_contact or inc.is_close_friend:
            self._sender_seed.set(inc.user_id, {"name": inc.sender_name, "username": inc.sender_username,
                                                "contact": inc.is_contact, "close_friend": inc.is_close_friend})
        exempt = self.mod.is_trusted(inc.user_id)
        self._spawn(self._touch_person(inc))

        voice = False
        if inc.voice_loader and self.s.voice_enabled and (inc.is_private or inc.is_mentioned):
            heard = await self._transcribe(inc)
            if heard:
                text, voice = heard, True
        lang = detect_lang(text)

        if exempt:
            self.stats["trusted_skipped"] += 1
        elif text and await self._apply_decision(inc, await self.mod.inspect(
                inc.user_id, text, lang, quote=inc.quote, kind="voice" if voice else "text"), text):
            return

        pic_loader = inc.image_loader or inc.thumb_loader
        pic: Optional[Tuple[bytes, str]] = None
        verdict: Optional[ImageVerdict] = None
        checking = self.s.image_moderation and self.mod.enabled
        if (pic_loader or inc.frames_loader) and self.s.vision_enabled:
            if pic_loader:
                try:
                    pic = await asyncio.wait_for(pic_loader(), timeout=25.0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pic = None
            frames_seen = (0, 0)
            if (checking and not exempt and inc.frames_loader and self.s.video_moderation
                    and inc.media_kind in ("video", "gif", "video_note")):
                try:
                    frames = await asyncio.wait_for(inc.frames_loader(self.s.video_frames), timeout=40.0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    frames = []
                if frames:
                    verdict, n_ok, n_all = await self.imod.classify_frames(frames, text, inc.user_id)
                    frames_seen = (n_ok, n_all)
                    if verdict is not None and pic is None:
                        pic = frames[0]
            if pic and verdict is None:
                verdict = await self.imod.classify(pic, text, inc.user_id)
            if verdict is not None and checking and not exempt:
                if await self._apply_decision(inc, await self.mod.inspect_image(
                        inc.user_id, verdict, lang, inc.media_kind, frames=frames_seen), text):
                    return
            elif (verdict is None and checking and not exempt and inc.image_loader
                  and inc.media_kind not in ("sticker", "gif")):
                self.stats["images_unchecked"] += 1
                tg_log.warning(f"user={inc.user_id} picture not checked — not answered")
                return
            elif (verdict is None and checking and not exempt and inc.thumb_loader and not inc.image_loader
                  and inc.media_kind in ("video", "video_note") and inc.frames_loader is None):
                pass

        wants_image = bool(inc.image_loader) and self.s.vision_enabled
        if wants_image and verdict is None and inc.media_kind in ("sticker", "gif"):
            wants_image = False

        ref = None
        if wants_image:
            async def _loaded(p=pic):
                return p
            ref = ImageRef(_loaded, verdict, inc.media_kind)
        tag = _MEDIA_TAGS.get(inc.media_kind, IMAGE_TAG)
        body = f"{tag} {text}".strip() if wants_image else (f"{VOICE_TAG} {text}" if voice else text)
        if inc.quote:
            who = "رسالة من صاحب الحساب" if inc.quote_mine else "رسالة"
            body = f"(رد على {who}: «{inc.quote[:160]}») {body}"

        reason = self._gate_reason(inc)
        if reason:
            if reason == "owner" and (text or wants_image):
                self._hold(inc.user_id, inc.chat_id, inc.message_id, body, ref)
            return

        if not text and not wants_image:
            if self.s.media_only_reply and self.presence.ai_allowed(inc.chat_id):
                await self._send_reply(inc.chat_id, media_reply(lang), inc.message_id, self.presence.ticket(inc.chat_id))
            return

        self._enqueue(inc, body, ref)

    async def _apply_decision(self, inc: Incoming, decision, text: str = "") -> bool:
        if decision.action == "ignore_blocked":
            self.stats["blocked_ignored"] += 1
            return True
        if decision.action == "ignore_spam":
            return True
        if decision.action == "warn":
            self.stats["moderation_warnings"] += 1
            await self.t.send(inc.chat_id, decision.message, None)
            self._notify_moderation(inc, decision, text)
            return True
        if decision.action == "block":
            self.stats["moderation_blocks"] += 1
            await self.t.send(inc.chat_id, decision.message, None)
            ok = await self.t.block(inc.user_id)
            if ok:
                await self.mod.db.mark_tg_blocked(inc.user_id)
            self._notify_moderation(inc, decision, text)
            if self.s.delete_chat_on_block and inc.is_private:
                self._spawn(self._delete_chat_later(inc.chat_id))
            return True
        return False

    on_moderation: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None

    def _notify_moderation(self, inc: Incoming, decision, text: str = "") -> None:
        cb = self.on_moderation
        if cb is None or not self.s.notify_moderation:
            return
        c = decision.classification
        info = {"user_id": inc.user_id, "name": inc.sender_name, "username": inc.sender_username,
                "action": decision.action, "count": decision.count, "kind": getattr(decision, "kind", "") or inc.media_kind or "text",
                "label": getattr(c, "label", ""), "reason": getattr(c, "reason", ""),
                "evidence": getattr(decision, "evidence", "") or text[:160], "chat_id": inc.chat_id}
        self._spawn(self._safe_cb(cb, info))

    async def _safe_cb(self, cb, info) -> None:
        try:
            await cb(info)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.warning(f"moderation alert failed: {type(e).__name__}")

    async def _touch_person(self, inc: Incoming) -> None:
        try:
            await self.mem.touch_person(inc.user_id, inc.sender_name, inc.sender_username,
                                        detect_lang(inc.text or "").dialect if inc.text else "")
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def forget_person(self, user_id: int) -> Dict[str, int]:
        was_blocked = self.mod.is_blocked(user_id)
        res = await self.mod.forget(user_id)
        self.mem.forget_runtime(user_id)
        for cache in (self._sender_cache, self._sender_seed, self._last_image, self._engaged):
            cache.discard(user_id)
        self._held.pop(user_id, None)
        self._sticker_for.pop(user_id, None)
        self._last_failure_reply.pop(user_id, None)
        self.imod._log.pop(user_id, None)
        if True:
            try:
                await self.t.unblock(user_id)
            except Exception:
                pass
        return res

    async def handle_edit(self, inc: Incoming) -> None:
        if (not self.s.moderate_edits or inc.user_id == self.owner_id or inc.is_bot or inc.user_id <= 0
                or not (inc.text or "").strip() or (inc.text or "").startswith(".")):
            return
        if not inc.is_private and not (inc.is_mentioned or inc.is_reply_to_me):
            return
        if self.mod.is_blocked(inc.user_id) or self.mod.is_trusted(inc.user_id):
            return
        key = (inc.chat_id, inc.message_id, hash(inc.text.strip()))
        if not self._seen.add_if_new(("edit",) + key):
            return
        text = inc.text.strip()
        await self._apply_decision(inc, await self.mod.inspect(inc.user_id, text, detect_lang(text), quote=inc.quote,
                                                               kind="edit"), text)

    def note_owner_engaged(self, chat_id: int) -> None:
        if chat_id and chat_id != self.owner_id:
            self.discard_held(chat_id)
        if self.s.trust_engaged_hours > 0 and chat_id and chat_id != self.owner_id:
            self._engaged.set(chat_id, True)

    def _tier_for(self, user_id: int, chat_id: int, contact: bool = False, close_friend: bool = False) -> str:
        tier = self.mod.trust_tier(user_id)
        if tier:
            return tier
        if self.s.trust_contacts:
            if close_friend:
                return "close_friend"
            if contact:
                return "contact"
        if self._engaged.get(chat_id):
            return "engaged"
        return ""

    async def _transcribe(self, inc: Incoming) -> str:
        try:
            audio = await asyncio.wait_for(inc.voice_loader(), timeout=25.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            audio = None
        if not audio:
            return ""
        data, mime, secs = audio
        text = await self.stt.transcribe(data, mime, secs, inc.user_id)
        self.stats["voice_transcribed" if text else "voice_failed"] += 1
        return text or ""

    async def _delete_chat_later(self, chat_id: int) -> None:
        try:
            if self.s.block_delete_delay > 0:
                await asyncio.sleep(self.s.block_delete_delay)
            await self.t.delete_chat(chat_id, revoke=self.s.delete_chat_revoke)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.warning(f"chat clean-up after block failed: {type(e).__name__}")

    def _gate_reason(self, inc: Incoming) -> str:
        if not self.ai_reply_enabled or (not inc.is_private and not self.s.reply_in_groups):
            self.stats["ai_skipped_other"] += 1
            return "other"
        why = self.presence.block_reason(inc.chat_id)
        if why:
            self.stats["ai_skipped_presence"] += 1
            ai_log.info(f"user={inc.user_id} not answered now: {why}"
                        + (" — held until he leaves" if why == "owner" and self.s.hold_replies else "")
                        + (f" [{self.presence.explain()}]" if why == "owner" else ""))
            return why
        if self.s.whitelist and inc.user_id not in self.s.whitelist:
            self.stats["ai_skipped_other"] += 1
            return "other"
        if not self._limiter.allow(inc.user_id):
            self.stats["ai_skipped_other"] += 1
            return "other"
        return ""

    def _ai_gate(self, inc: Incoming) -> bool:
        return self._gate_reason(inc) == ""

    def _hold(self, user_id: int, chat_id: int, message_id: int, body: str, ref: Optional["ImageRef"]) -> None:
        if not self.s.hold_replies:
            return
        if ref is not None and not self.s.vision_direct_reply:
            ref = ImageRef(_no_bytes, ref.verdict, ref.kind)
        if user_id not in self._held and len(self._held) >= 100:
            self._held.pop(next(iter(self._held)))
        lst = self._held.setdefault(user_id, [])
        lst.append(_Held(chat_id, message_id, body, ref, time.monotonic()))
        del lst[:-6]
        self.stats["held"] += 1

    def discard_held(self, chat_id: int) -> None:
        for uid in list(self._held):
            kept = [h for h in self._held[uid] if h.chat_id != chat_id]
            if kept:
                self._held[uid] = kept
            else:
                self._held.pop(uid, None)

    def _on_wake(self) -> None:
        if self._held and not self._closing:
            self._spawn(self._flush_held())

    async def _flush_held(self) -> None:
        now, ttl = time.monotonic(), self.s.hold_minutes * 60.0
        for uid in list(self._held):
            items = [h for h in self._held.pop(uid, []) if now - h.ts <= ttl]
            if not items or self.mod.is_blocked(uid) or not self.ai_reply_enabled:
                continue
            if not self.presence.ai_allowed(items[-1].chat_id):
                continue
            ai_log.info(f"user={uid} answering {len(items)} held message(s) — the owner left")
            self.stats["held_answered"] += 1
            for h in items:
                self._enqueue(Incoming(chat_id=h.chat_id, message_id=h.message_id, user_id=uid, text=""), h.body, h.ref)

    def _enqueue(self, inc: Incoming, text: str, image: Optional[ImageRef] = None) -> None:
        b = self._bursts.get(inc.user_id)
        now = time.monotonic()
        if b is None:
            if len(self._bursts) >= self.s.max_pending_users:
                self.stats["ai_skipped_other"] += 1
                return
            b = self._bursts[inc.user_id] = _Burst(chat_id=inc.chat_id, first_msg_id=inc.message_id, first_at=now)
        else:
            self.stats["coalesced_msgs"] += 1
        if len(b.texts) < 12:
            b.texts.append(sanitize_user_text(text, self.s.max_input_chars))
        if image is not None and len(b.images) < 3:
            b.images.append(image)
        b.last_msg_id, b.last_at, b.chat_id = inc.message_id, now, inc.chat_id
        if b.worker is None or b.worker.done():
            b.worker = asyncio.create_task(self._worker(inc.user_id), name=f"tfe-user-{inc.user_id}")
            self._bg.add(b.worker)
            b.worker.add_done_callback(self._bg.discard)

    async def _worker(self, user_id: int) -> None:
        try:
            while not self._closing:
                b = self._bursts.get(user_id)
                if b is None or not b.texts:
                    break
                while True:
                    quiet = time.monotonic() - b.last_at
                    total = time.monotonic() - b.first_at
                    if quiet >= self.s.coalesce_window or total >= max(self.s.coalesce_max_wait, self.s.coalesce_window):
                        break
                    await asyncio.sleep(min(0.1, max(0.01, self.s.coalesce_window - quiet)))
                texts, chat_id, reply_to = b.texts, b.chat_id, b.last_msg_id
                images, b.images = b.images, []
                b.texts = []
                b.first_at = b.last_at = time.monotonic()
                await self._process(user_id, chat_id, texts, reply_to, b, images)
                if not b.texts:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.error(f"worker for user={user_id} crashed: {type(e).__name__}: {e}")
        finally:
            b = self._bursts.get(user_id)
            if b is not None and not b.texts:
                self._bursts.pop(user_id, None)

    async def _process(self, user_id: int, chat_id: int, texts: List[str], reply_to: int, burst: _Burst,
                       images: Optional[list] = None) -> None:
        joined = "\n".join(texts)
        images = list(images or [])
        regen = 0
        while True:
            if self.mod.is_blocked(user_id):
                return
            if not self.presence.ai_allowed(chat_id) or not self.ai_reply_enabled:
                self.stats["discarded_presence"] += 1
                if not self._hold_burst(user_id, chat_id, reply_to, joined, images):
                    await self.mem.remember_user_only(user_id, joined)
                return
            ticket = self.presence.ticket(chat_id)
            gen = asyncio.create_task(self._generate(user_id, chat_id, joined, images))
            self.presence.track(gen)
            try:
                reply = await gen
            except asyncio.CancelledError:
                cur = asyncio.current_task()
                if gen.cancelled() and not (cur and getattr(cur, "cancelling", lambda: 0)()):
                    self.stats["discarded_presence"] += 1
                    ai_log.info(f"user={user_id} generation cancelled (owner online) — nothing sent")
                    if not self._hold_burst(user_id, chat_id, reply_to, joined, images):
                        await self.mem.remember_user_only(user_id, joined)
                    return
                gen.cancel()
                raise
            except AIUnavailable as e:
                self.stats["ai_failures"] += 1
                err_log.warning(f"user={user_id} AI unavailable: {e.reason}")
                await self._maybe_failure_reply(user_id, chat_id, reply_to, joined, ticket)
                return
            except Exception as e:
                self.stats["ai_failures"] += 1
                err_log.error(f"user={user_id} generation error: {type(e).__name__}: {e}")
                await self._maybe_failure_reply(user_id, chat_id, reply_to, joined, ticket)
                return

            if not reply and not self._sticker_for.get(user_id):
                return
            if burst.texts and regen < 1 and not self.mod.is_blocked(user_id):
                regen += 1
                self.stats["regenerated"] += 1
                joined = joined + "\n" + "\n".join(burst.texts)
                reply_to = burst.last_msg_id
                burst.texts = []
                images += burst.images
                burst.images = []
                continue

            await self._deliver(user_id, chat_id, reply_to, joined, reply, ticket, images)
            return

    def _hold_burst(self, user_id: int, chat_id: int, reply_to: int, joined: str, images: list) -> bool:
        if (self.mod.is_blocked(user_id) or not self.ai_reply_enabled or not self.s.hold_replies
                or self.presence.block_reason(chat_id) != "owner"):
            return False
        self._hold(user_id, chat_id, reply_to, joined, images[-1] if images else None)
        return True

    def _now(self) -> datetime:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(self.s.tz))
        except Exception:
            return datetime.now()

    def _relation_note(self, user_id: int, text: str, mctx) -> str:
        has_hist = bool(mctx.summary or mctx.facts or mctx.history)
        prof = getattr(mctx, "profile", None) or {}
        return relationship.build_relation_note(
            self.persona, user_id, text, now=self._now(), last_ts=getattr(mctx, "last_ts", 0.0), now_ts=time.time(),
            has_history=has_hist, history=mctx.history, facts=mctx.facts, summary=mctx.summary,
            msg_count=int(prof.get("msg_count") or 0))

    def _today(self) -> date:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(self.s.tz)).date()
        except Exception:
            return date.today()

    async def _load_images(self, user_id: int, joined: str, refs: list) -> Tuple[list, bool, Optional[ImageVerdict]]:
        direct = self.s.vision_direct_reply
        pics: List[Tuple[bytes, str]] = []
        verdict: Optional[ImageVerdict] = None
        kind = ""
        for ref in refs[:2]:
            verdict = ref.verdict or verdict
            kind = ref.kind or kind
            if direct:
                try:
                    got = await asyncio.wait_for(ref.loader(), timeout=25.0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    got = None
                if got:
                    pics.append(got)
        if verdict is not None or pics:
            keep = pics[-1] if pics and len(pics[-1][0]) <= 1_500_000 else None
            self._last_image.set(user_id, (keep, verdict))
        elif not refs and len(joined) <= 160 and _IMAGE_REF.search(joined):
            cached = self._last_image.get(user_id)
            if cached:
                if direct and cached[0]:
                    pics.append(cached[0])
                verdict = cached[1]
        self._last_kind = kind
        return pics, bool(refs), verdict

    @staticmethod
    def _attach_images(messages: List[Dict[str, Any]], pics: List[Tuple[bytes, str]]) -> List[Dict[str, Any]]:
        out = list(messages)
        last = out[-1]
        parts: List[Dict[str, Any]] = [{"type": "text", "text": str(last.get("content") or IMAGE_TAG)}]
        for data, mime in pics:
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}})
        out[-1] = {"role": last["role"], "content": parts}
        return out

    async def _generate(self, user_id: int, chat_id: int, joined: str, images: Optional[list] = None) -> str:
        t0 = time.monotonic()
        self._sticker_for.pop(user_id, None)
        want_search = self.search.enabled and needs_search(joined)

        async def _sender() -> Dict[str, Any]:
            seed = dict(self._sender_seed.get(user_id) or {})
            extra = self._sender_cache.get(user_id)
            if extra is None:
                try:
                    got = await asyncio.wait_for(self.t.sender_info(user_id), timeout=4.5)
                except Exception:
                    got = {}
                extra = got or {}
                if got:
                    self._sender_cache.set(user_id, got)
            info = dict(extra)
            for k in ("name", "username"):
                if seed.get(k):
                    info[k] = seed[k]
            if seed.get("contact"):
                info["contact"] = True
            if seed.get("close_friend"):
                info["close_friend"] = True
            return info

        async def _search():
            if not want_search:
                return None
            return await self.search.search(joined)

        info, mctx, sres, (pics, had_images, verdict) = await asyncio.gather(
            _sender(), self.mem.load(user_id, joined), _search(), self._load_images(user_id, joined, images or []))

        user_hist = [m.get("content", "") for m in mctx.history if m.get("role") == "user"]
        lang = detect_lang_ctx(joined, user_hist)
        prompt_text = _for_model(joined)
        history = []
        for m in mctx.history:
            if m.get("role") == "assistant":
                c = canon_sticker(strip_foreign_cjk(m.get("content", ""), joined, *user_hist))
                if c:
                    history.append({**m, "content": c})
            else:
                history.append({**m, "content": _for_model(m.get("content", ""))} if m.get("role") == "user" else m)

        bits: List[str] = []
        clean_name, name_ok = clean_display_name(info.get("name", ""))
        if name_ok:
            shown = f" (يظهر عنده مزخرف: {info['name']})" if clean_name != (info.get("name") or "").strip() else ""
            bits.append(f"الاسم على تيليجرام: {clean_name}{shown}")
        elif info.get("name"):
            bits.append(f"اسمه على تيليجرام رموز/زخرفة بدون حروف مقروءة: {info['name']}")
        if info.get("username"):
            bits.append(f"اليوزر: @{info['username']}")
        if info.get("bio") and self.s.include_sender_bio:
            bits.append(f"البايو: {shorten(info['bio'], 200)}")
        if info.get("contact"):
            bits.append("موجود بجهات اتصال صاحب الحساب")
        known = self._returning_line(mctx)
        if known:
            bits.append(known)
        tier = self._tier_for(user_id, chat_id, bool(info.get("contact")), bool(info.get("close_friend")))
        partner = relationship.is_partner(self.persona, user_id)
        if tier in _TIER_LINES and not partner:
            bits.append(_TIER_LINES[tier])
        sender_ctx = "\n".join(bits)

        name_hint = _name_note(info.get("name", ""), bool(_NAME_Q.search(joined)))
        media_kind = getattr(self, "_last_kind", "")
        if pics:
            note = image_note(verdict, sees_image=True, media_kind=media_kind)
        elif verdict is not None:
            note = image_note(verdict, sees_image=False, media_kind=media_kind)
        else:
            note = IMAGE_FAILED_NOTE if had_images else ""
        sticker_line = self._sticker_line()
        if sticker_line:
            note = f"{note}\n{sticker_line}".strip()
        if name_hint:
            note = f"{note}\n{name_hint}".strip()
        profile_hint = _profile_note(info, user_id, self.owner_id, self.persona.get("display_name") or "صاحب الحساب",
                                     joined, self._today())
        if profile_hint:
            note = f"{note}\n{profile_hint}".strip()
        inp = PromptInputs(
            persona=self.persona, lang=lang, history=history, user_text=prompt_text, sender_ctx=sender_ctx,
            facts=mctx.facts, summary=mctx.summary, web=(sres.text if sres and sres.ok else ""),
            web_failed=bool(want_search and not (sres and sres.ok)), max_input_chars=self.s.max_input_chars * 2,
            media_note=note, relation_note=self._relation_note(user_id, joined, mctx))
        messages = build_messages(inp)
        if pics:
            comp = None
            try:
                comp = await self.router.complete(
                    Role.VISION, self._attach_images(messages, pics), max_tokens=self.s.chat_max_tokens,
                    temperature=self.s.chat_temperature, timeout=self.s.vision_timeout,
                    deadline=self.s.vision_timeout + 5.0)
            except AIUnavailable as e:
                err_log.warning(f"user={user_id} vision unavailable ({e.reason}) — answering without the image")
                inp.media_note = IMAGE_FAILED_NOTE
                messages = build_messages(inp)
            if comp is None:
                comp = await self.router.complete(
                    Role.CHAT, messages, max_tokens=self.s.chat_max_tokens, temperature=self.s.chat_temperature,
                    timeout=self.s.ai_timeout, deadline=self.s.max_ai_latency)
        else:
            role = self.router.pick_role(joined, has_search=want_search)
            comp = await self.router.complete(
                role, messages, max_tokens=self.s.chat_max_tokens, temperature=self.s.chat_temperature,
                timeout=self.s.ai_timeout, deadline=self.s.max_ai_latency)
        raw, sticker = extract_sticker(comp.text)
        reply = polish_reply(raw, self.s.max_reply_chars)
        reply = strip_foreign_cjk(reply, joined, *user_hist)
        if sticker and self.s.stickers_enabled and not self.draft_mode:
            self._sticker_for[user_id] = sticker
        if not reply and not self._sticker_for.get(user_id):
            reply = _first_emoji(joined) or "🙂"
        ai_log.info(f"user={user_id} model={comp.model} role={comp.role} latency={time.monotonic() - t0:.2f}s "
                    f"search={'yes' if want_search else 'no'} memory_facts={len(mctx.facts)} "
                    f"image={'seen' if pics else ('described' if verdict is not None else 'no')} name={'yes' if name_ok else ('symbols' if info.get('name') else 'no')} "
                    f"bio={'yes' if info.get('bio') else 'no'} bday={'yes' if info.get('birthday') else 'no'}")
        return reply

    async def _typing_pause(self, chat_id: int, reply: str) -> None:
        cap = self.s.max_typing_delay
        if cap <= 0:
            return
        secs = min(cap, 0.25 + len(reply) * (0.012 if self.s.fast_reply else 0.03), 0.8 if self.s.fast_reply else cap)
        try:
            await self.t.typing(chat_id, secs)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(min(secs, 0.5))

    async def _deliver(self, user_id: int, chat_id: int, reply_to: int, user_text: str, reply: str, ticket,
                       images: Optional[list] = None) -> None:
        sticker = self._sticker_for.pop(user_id, "")
        if not self.draft_mode and reply:
            await self._typing_pause(chat_id, reply)
        if not ticket.valid() or self.mod.is_blocked(user_id) or not self.ai_reply_enabled:
            self.stats["discarded_presence"] += 1
            ai_log.info(f"user={user_id} reply discarded (owner online / state changed) — not sent")
            if not self._hold_burst(user_id, chat_id, reply_to, user_text, list(images or [])):
                await self.mem.remember_user_only(user_id, user_text)
            return
        if self.draft_mode:
            info = self._sender_seed.get(user_id) or self._sender_cache.get(user_id) or {}
            name = info.get("name") or str(user_id)
            await self.t.notify_owner(f"📩 من {name} ({user_id}):\n{user_text[:500]}\n\n🤖 رد مقترح:\n{reply}\n\n"
                                      f"لإرساله: .send {user_id} {reply}")
            self.stats["drafts"] += 1
            await self.mem.remember_turn(user_id, user_text, None)
            return
        partner = relationship.is_partner(self.persona, user_id)
        sent = await self._send_reply(chat_id, reply, reply_to, ticket, bubbles=partner) if reply else True
        sticker_sent = False
        if sent and sticker and ticket.valid():
            if not self._sticker_ok(chat_id):
                if not reply:
                    sent = False
            elif await self.t.send_sticker(chat_id, sticker):
                self.stats["stickers_sent"] += 1
                sticker_sent = True
            elif not reply:
                reply = sticker
                sent = await self._send_reply(chat_id, reply, reply_to, ticket)
        if sent:
            self.stats["replies"] += 1
            stored = f"{reply} [[ستيكر:{sticker}]]".strip() if sticker_sent else reply
            await self.mem.remember_turn(user_id, user_text, stored)
            self._spawn(self.mem.maybe_maintain(user_id, every=2 if partner else 4))
        else:
            await self.mem.remember_user_only(user_id, user_text)

    def _sticker_line(self) -> str:
        if not self.s.stickers_enabled or self.draft_mode:
            return ""
        em = getattr(self.t, "sticker_emojis", None)
        try:
            emojis = em(24) if em else []
        except Exception:
            emojis = []
        if not emojis:
            return ""
        return ("ستيكراتك (مال صاحب الحساب) مربوطة بهذي الإيموجيات، فإذا تريد ترد بستيكر اختار واحد منها بالضبط: "
                + " ".join(emojis))

    def _sticker_ok(self, chat_id: int) -> bool:
        if not self.s.stickers_enabled or self._sticker_last.get(chat_id):
            return False
        self._sticker_last.set(chat_id, True)
        return True

    async def _send_reply(self, chat_id: int, text: str, reply_to: Optional[int], ticket, bubbles: bool = False) -> bool:
        if bubbles:
            ok_any = False
            parts = relationship.split_bubbles(text)
            for i, part in enumerate(parts):
                if i > 0:
                    if not ticket.valid():
                        break
                    await self._typing_pause(chat_id, part)
                    await asyncio.sleep(min(self.s.max_typing_delay, relationship.bubble_delay(part))
                                        if self.s.max_typing_delay > 0 else 0)
                    if not ticket.valid():
                        break
                ok = await self._send_reply(chat_id, part, reply_to if i == 0 else None, ticket)
                ok_any = ok_any or ok
                if not ok:
                    break
            return ok_any
        parts = split_message(text, 3800) or []
        ok_any = False
        for i, part in enumerate(parts):
            if i > 0 and not ticket.valid():
                break
            ok = await self.t.send(chat_id, part, reply_to if i == 0 else None)
            ok_any = ok_any or ok
            if not ok:
                break
            if i + 1 < len(parts):
                await asyncio.sleep(0.4)
        return ok_any

    async def _maybe_failure_reply(self, user_id: int, chat_id: int, reply_to: int, text: str, ticket) -> None:
        if self.s.failure_reply_mode != "short" or self.draft_mode:
            await self.mem.remember_user_only(user_id, text)
            return
        now = time.monotonic()
        if now - self._last_failure_reply.get(user_id, -1e9) < 900 or now - self._last_global_failure_reply < 120:
            await self.mem.remember_user_only(user_id, text)
            return
        if not ticket.valid():
            return
        self._last_failure_reply[user_id] = now
        self._last_global_failure_reply = now
        if len(self._last_failure_reply) > 2000:
            self._last_failure_reply.clear()
        await self.t.send(chat_id, failure_reply(detect_lang(text)), reply_to)
        await self.mem.remember_user_only(user_id, text)

    @staticmethod
    def _returning_line(mctx) -> str:
        p = getattr(mctx, "profile", None) or {}
        n = int(p.get("msg_count") or 0)
        first = float(p.get("first_seen") or 0)
        if n < 4 and not mctx.summary and not mctx.facts:
            return ""
        bits = ["هذا شخص تعرفه من قبل (مو أول مرة تحچي وياه)"]
        if first:
            days = int((time.time() - first) // 86400)
            bits.append("بدأتم الحچي اليوم" if days < 1 else f"تحچون من {days} يوم")
        if n:
            bits.append(f"كتبلك حوالي {n} رسالة")
        line = "، ".join(bits) + ". تصرف وياه كأنك تتذكره وتتذكر حچيكم القديم (الملخص والمعلومات المحفوظة) بدون ما تذكر إنك تقرأ ملف."
        return line

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def status(self) -> Dict[str, Any]:
        return {**self.stats, **{f"image_{k}": v for k, v in self.imod.stats.items()},
                "pending_users": len(self._bursts), "background_tasks": len(self._bg),
                "draft_mode": self.draft_mode, "ai_reply_enabled": self.ai_reply_enabled}

    async def shutdown(self) -> None:
        self._closing = True
        tasks = [t for t in list(self._bg) if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

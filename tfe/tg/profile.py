from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from telethon import errors
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.users import GetFullUserRequest

from ..config import Settings
from ..logs import ERROR as err_log, TELEGRAM as log

BIO_PHRASES = [
    "👑 هدوء الملوك", "⚜️ فخامة لا تضاهى", "💎 جوهرة الطموح",
    "🦅 صقر السماء", "🔱 أسطورة لا تنتهي", "⚡ سرعة وابتكار",
    "🛡 درع الحماية", "🔥 نار الإبداع", "✨ بصمة استثنائية",
    "🏛 رجل المواقف", "🌟 نجم ساطع", "⚔️ قوة وذكاء",
    "🌌 سيد التحديات", "🧿 عين الحقيقة", "🕊️ روح طليقة",
    "🎯 دقة الإنجاز", "🚀 طموح بلا حدود", "🏆 صانع المجد",
    "🦁 شجاعة الملوك", "🎩 أناقة القيادة", "🌙 سكون الليل",
    "☀️ إشراقة الفجر", "🦅 هيبة الصقور", "💠 نقاء الماس",
    "🗡 حدّ العزيمة", "🌊 عمق الحكمة", "🎖 رمز الوفاء",
    "🏅 وسام التميّز", "🔮 نظرة المستقبل", "🌠 شهاب الليل",
]
_BOLD = str.maketrans("0123456789", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵")


def to_bold_numerals(text: str) -> str:
    return text.translate(_BOLD)


def build_time_string(tz: str, fmt: str = "12", now: Optional[datetime] = None) -> str:
    now = now or datetime.now(ZoneInfo(tz))
    if fmt == "12":
        h = now.strftime("%I:%M")
        return f"{to_bold_numerals(h)} {'PM' if now.hour >= 12 else 'AM'}"
    return to_bold_numerals(now.strftime("%H:%M"))


def rotating_bio(tz: str, bots: str, interval_minutes: int, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(ZoneInfo(tz))
    block = (now.hour * 60 + now.minute) // max(1, interval_minutes)
    bio = f"{BIO_PHRASES[block % len(BIO_PHRASES)]} | {bots}".strip(" |")
    return bio if len(bio) <= 70 else bio[:67] + "..."


class ProfileUpdater:
    REFRESH_EVERY = 1800.0

    def __init__(self, client: Any, settings: Settings):
        self.client = client
        self.s = settings
        self._last_name: Optional[str] = None
        self._about: Optional[str] = None
        self._identity_at = 0.0
        self._paused_until = 0.0
        self.me_username = ""
        self.me_first_name = ""

    async def _refresh_identity(self, force: bool = False) -> None:
        if not force and self._identity_at and time.monotonic() - self._identity_at < self.REFRESH_EVERY:
            return
        me = await self.client.get_me()
        self._last_name = getattr(me, "last_name", None) or ""
        self.me_first_name = getattr(me, "first_name", "") or ""
        self.me_username = getattr(me, "username", "") or ""
        try:
            full = await self.client(GetFullUserRequest(me))
            self._about = getattr(full.full_user, "about", "") or ""
        except Exception:
            self._about = None
        self._identity_at = time.monotonic()

    def _flood(self, e: "errors.FloodWaitError") -> None:
        secs = int(getattr(e, "seconds", 60) or 60)
        self._paused_until = time.monotonic() + secs + 5
        err_log.warning(f"profile update FloodWait {secs}s — updater paused")

    async def update_name_time(self) -> None:
        if time.monotonic() < self._paused_until:
            return
        try:
            await self._refresh_identity()
            new_last = build_time_string(self.s.tz, self.s.time_format)
            if self._last_name != new_last:
                await self.client(UpdateProfileRequest(last_name=new_last))
                self._last_name = new_last
                log.info(f"name time updated: {new_last}")
        except errors.FloodWaitError as e:
            self._flood(e)
        except Exception as e:
            self._identity_at = 0.0
            err_log.warning(f"name update failed ({type(e).__name__})")

    async def update_bio(self) -> None:
        if time.monotonic() < self._paused_until:
            return
        try:
            await self._refresh_identity()
            bio = rotating_bio(self.s.tz, self.s.bio_bots, self.s.bio_interval_minutes)
            if self._about != bio:
                await self.client(UpdateProfileRequest(about=bio))
                self._about = bio
                log.info("bio updated")
        except errors.FloodWaitError as e:
            self._flood(e)
        except Exception as e:
            self._identity_at = 0.0
            err_log.warning(f"bio update failed ({type(e).__name__})")

    def current_bio(self) -> str:
        return self._about or ""

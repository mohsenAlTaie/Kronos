from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .tg.commands import _TIER_LABEL, _split_ref_tier

Button = Union[Tuple[str, str], Tuple[str, str, Optional[str]]]
Rows = List[List[Button]]


class ButtonStyle:
    PRIMARY = "primary"
    SUCCESS = "success"
    DANGER = "danger"


P, S, D = ButtonStyle.PRIMARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER
_NEXT_TIER = {"vip": "friend", "friend": "love", "love": "vip"}
MAX_ROWS = 20
LINE = "━━━━━━━━━━━━━━━━"

_KIND_AR = {"text": "💬 نص", "voice": "🎙 صوت", "photo": "🖼 صورة", "image": "🖼 صورة", "image_doc": "🖼 صورة",
            "sticker": "🎭 ستيكر", "gif": "🎞 GIF", "video": "🎞 فيديو", "video_note": "🎞 فيديو", "edit": "✏️ تعديل رسالة",
            "manual": "✋ يدوي"}
_ACTION_AR = {"warn": "⚠️ تحذير", "block": "🚫 حظر", "manual_block": "🚫 حظر يدوي", "unblock": "🔓 رفع حظر",
              "unwarn": "✅ فك تحذير", "already_blocked": "🚫", "pass": "✔️"}
_LABEL_AR = {"SEXUAL_ABUSE": "محتوى جنسي", "THREAT": "تهديد", "PROFANITY": "شتم", "INSULT": "إهانة",
             "HARASSMENT": "تحرش/كراهية", "HATE": "كراهية", "MANUAL": "يدوي", "SPAM": "سبام"}


def ago(ts: float, now: Optional[float] = None) -> str:
    sec = max(0, int((now or time.time()) - float(ts or 0)))
    if not ts:
        return "—"
    if sec < 90:
        return "الآن"
    if sec < 3600:
        return f"قبل {sec // 60} دقيقة"
    if sec < 86400:
        return f"قبل {sec // 3600} ساعة"
    return f"قبل {sec // 86400} يوم"


def person_name(p: Dict[str, Any]) -> str:
    name = " ".join(x for x in (p.get("first_name") or "", p.get("last_name") or "") if x).strip()
    return name or "بدون اسم"


def person_line(p: Dict[str, Any]) -> str:
    user = f"@{p['username']}" if p.get("username") else "بدون يوزر"
    return f"👤 {person_name(p)} · {user}\n🆔 {p['user_id']}"


def _short(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _label(row: Dict[str, Any]) -> str:
    return _LABEL_AR.get((row.get("label") or "").upper(), row.get("label") or "—")


def _reason_ar(row: Dict[str, Any]) -> str:
    kind = _KIND_AR.get(row.get("kind") or "", "")
    lab = _label(row)
    return f"{lab}{' — ' + kind if kind else ''}"


def _pager(prefix: str, page: int, pages: int) -> List[Button]:
    row: List[Button] = []
    if page > 0:
        row.append(("◀️ السابق", f"{prefix}:{page - 1}", P))
    row.append((f"📄 {page + 1}/{max(pages, 1)}", "noop", None))
    if page + 1 < pages:
        row.append(("التالي ▶️", f"{prefix}:{page + 1}", P))
    return row


class ControlPanel:
    def __init__(self, app: Any):
        self.app = app
        self.adding = False

    @property
    def page_size(self) -> int:
        return max(2, min(8, int(getattr(self.app.s, "control_page_size", 5) or 5)))

    async def menu(self, note: str = "") -> Tuple[str, Rows]:
        a = self.app
        db = a.db
        blocked = await db.blocked_count()
        warns = await db.warnings_count(a.s.warning_ttl_hours)
        st = await db.stats()
        vips = len(await a.moderation.trust_list())
        prot = a.moderation.enabled
        ai_on = a.pipeline.ai_reply_enabled and a.presence.ai_manual_enabled if a.pipeline else False
        head = f"{note}\n\n" if note else ""
        text = (f"{head}🛡️ لوحة تحكم الحماية\n{LINE}\n"
                f"🚫 المحظورين: {blocked}\n⚠️ تحذيرات فعّالة: {warns}\n⭐ المميزين (VIP): {vips}\n"
                f"🧠 أشخاص محفوظين: {st.get('profiles', 0)}\n{LINE}\n"
                f"🛡️ الحماية: {'شغالة ✅' if prot else 'متوقفة ❌'}\n🤖 ردود الـAI: {'شغالة ✅' if ai_on else 'متوقفة ❌'}\n"
                "ℹ️ الـVIP فقط مستثنى من الحظر — أي شخص ثاني ينحظر إذا أرسل شتم أو صور/فيديوهات غير لائقة.")
        rows: Rows = [
            [(f"🚫 المحظورين ({blocked})", "b:0", D), (f"⚠️ التحذيرات ({warns})", "w:0", P)],
            [(f"⭐ المميزين ({vips})", "r", P), (f"🧠 الأشخاص ({st.get('profiles', 0)})", "p:0", P)],
            [(f"🛡️ الحماية {'✅' if prot else '❌'}", "sm:mod", S if prot else D),
             (f"🤖 الـAI {'✅' if ai_on else '❌'}", "sm:ai", S if ai_on else D)],
            [("⚙️ الإعدادات", "s", P), ("📊 الإحصائيات", "st", P)],
            [("🔄 تحديث", "m", S)],
        ]
        return text, rows

    async def blocked_view(self, page: int = 0, note: str = "") -> Tuple[str, Rows]:
        db, n = self.app.db, self.page_size
        total = await db.blocked_count()
        pages = max(1, -(-total // n))
        page = max(0, min(page, pages - 1))
        rows_db = await db.blocked_page(page * n, n)
        head = f"{note}\n\n" if note else ""
        if not rows_db:
            return f"{head}🚫 المحظورين\n{LINE}\nما كو أحد محظور ✅", [[("🏠 الرئيسية", "m", P)]]
        lines = [f"{head}🚫 المحظورين ({total}) — صفحة {page + 1}/{pages}", LINE]
        buttons: Rows = []
        for i, r in enumerate(rows_db, start=page * n + 1):
            ev = _short((r.get("evidence") or "").strip(), 110)
            lines.append(f"{i}. {person_line(r)}\n📌 السبب: {_reason_ar(r)}"
                         + (f"\n🧾 الدليل: {ev}" if ev else "")
                         + f"\n🕒 {ago(r.get('blocked_at'))} · مخالفات: {r.get('offences') or 1}\n{LINE}")
            nm = _short(person_name(r), 9)
            buttons.append([(f"🔓 رفع الحظر · {nm}", f"ub:{r['user_id']}:{page}", S), ("🔎 تفاصيل", f"i:{r['user_id']}:b{page}", P)])
        buttons.append(_pager("b", page, pages))
        buttons.append([("🏠 الرئيسية", "m", P)])
        return "\n".join(lines), buttons

    async def warnings_view(self, page: int = 0, note: str = "") -> Tuple[str, Rows]:
        a, n = self.app, self.page_size
        ttl = a.s.warning_ttl_hours
        total = await a.db.warnings_count(ttl)
        pages = max(1, -(-total // n))
        page = max(0, min(page, pages - 1))
        rows_db = await a.db.warnings_page(ttl, page * n, n)
        head = f"{note}\n\n" if note else ""
        if not rows_db:
            return f"{head}⚠️ التحذيرات\n{LINE}\nما كو تحذيرات فعّالة ✅", [[("🏠 الرئيسية", "m", P)]]
        lines = [f"{head}⚠️ التحذيرات الفعّالة ({total}) — صفحة {page + 1}/{pages}",
                 f"(تنتهي تلقائياً بعد {ttl:g} ساعة)", LINE]
        buttons: Rows = []
        for i, r in enumerate(rows_db, start=page * n + 1):
            ev = _short((r.get("evidence") or "").strip(), 110)
            left = max(0.0, ttl * 3600 - (time.time() - float(r.get("last_ts") or 0))) / 3600
            lines.append(f"{i}. {person_line(r)}\n📌 السبب: {_reason_ar(r)} · تحذيرات: {r.get('count')}"
                         + (f"\n🧾 الدليل: {ev}" if ev else "")
                         + f"\n🕒 {ago(r.get('last_ts'))} · ينتهي بعد {left:.0f} ساعة\n{LINE}")
            nm = _short(person_name(r), 8)
            buttons.append([(f"✅ فك التحذير · {nm}", f"uw:{r['user_id']}:{page}", S),
                            ("🚫 حظر", f"bk:{r['user_id']}:{page}", D), ("🔎", f"i:{r['user_id']}:w{page}", P)])
        buttons.append(_pager("w", page, pages))
        buttons.append([("🏠 الرئيسية", "m", P)])
        return "\n".join(lines), buttons

    async def person_view(self, uid: int, back: str = "m", note: str = "") -> Tuple[str, Rows]:
        a = self.app
        card = await a.moderation.person_card(uid)
        prof = dict(card["profile"] or {})
        prof["user_id"] = uid
        head = f"{note}\n\n" if note else ""
        state = []
        if card["blocked"] is not None or a.moderation.is_blocked(uid):
            state.append("🚫 محظور")
        if card["warnings"]:
            state.append(f"⚠️ تحذيرات فعّالة: {card['warnings']}")
        if card["vip"]:
            state.append(_TIER_LABEL.get(card["vip"][0], "⭐ مميز"))
        if not state:
            state.append("✅ طبيعي")
        o = card["overview"]
        lines = [f"{head}🔎 تفاصيل الشخص\n{LINE}", person_line(prof), "الحالة: " + " | ".join(state)]
        if prof.get("first_seen"):
            lines.append(f"📅 أول حچي: {ago(prof['first_seen'])} · آخر ظهور: {ago(prof.get('last_seen'))} · رسائله: {prof.get('msg_count', 0)}")
        lines.append(f"🧠 محفوظ عنده: {o.get('messages', 0)} رسالة، {len(o.get('facts') or [])} معلومة")
        if o.get("summary"):
            lines.append(f"📝 {_short(o['summary'], 220)}")
        for f in (o.get("facts") or [])[:4]:
            lines.append(f"  • {_short(f['fact'], 90)}")
        if card["events"]:
            lines += [LINE, "📜 آخر الأحداث:"]
            for e in card["events"]:
                ev = _short(e.get("evidence") or e.get("reason") or "", 80)
                lines.append(f"{_ACTION_AR.get(e['action'], e['action'])} · {_KIND_AR.get(e.get('kind') or '', '')} "
                             f"{_LABEL_AR.get((e.get('label') or '').upper(), '')} · {ago(e['ts'])}" + (f"\n   {ev}" if ev else ""))
        buttons: Rows = []
        blocked = a.moderation.is_blocked(uid)
        row1: List[Button] = []
        if blocked:
            row1.append(("🔓 رفع الحظر", f"ub:{uid}:i", S))
        else:
            row1.append(("🚫 حظر", f"bk:{uid}:i", D))
        if card["warnings"]:
            row1.append(("✅ فك التحذير", f"uw:{uid}:i", S))
        buttons.append(row1)
        buttons.append([("⭐ إضافة VIP" if not card["vip"] else "⭐ VIP ✔", f"vp:{uid}", P),
                        ("🧹 نسيان الشخص", f"fg:{uid}", D)])
        buttons.append([("↩️ رجوع", _back_data(back), P), ("🏠 الرئيسية", "m", P)])
        return "\n".join(lines), buttons

    async def people_view(self, page: int = 0) -> Tuple[str, Rows]:
        a, n = self.app, self.page_size
        total = await a.db.profile_count()
        pages = max(1, -(-total // n))
        page = max(0, min(page, pages - 1))
        rows_db = await a.db.recent_profiles(n, page * n)
        if not rows_db:
            return f"🧠 الأشخاص المحفوظين\n{LINE}\nما انحفظ أحد لحد الآن.", [[("🏠 الرئيسية", "m", P)]]
        lines = [f"🧠 الأشخاص اللي يتذكرهم البوت ({total}) — صفحة {page + 1}/{pages}", LINE]
        buttons: Rows = []
        for r in rows_db:
            flag = "🚫 " if a.moderation.is_blocked(r["user_id"]) else ("⭐ " if a.moderation.is_trusted(r["user_id"]) else "")
            lines.append(f"{flag}{person_line(r)}\n💬 رسائل: {r.get('msg_count', 0)} · محفوظ: {r.get('stored', 0)} · معلومات: {r.get('facts', 0)}"
                         f" · آخر ظهور {ago(r.get('last_seen'))}\n{LINE}")
            buttons.append([(f"🔎 {_short(person_name(r), 14)}", f"i:{r['user_id']}:p{page}", P)])
        buttons.append(_pager("p", page, pages))
        buttons.append([("🏠 الرئيسية", "m", P)])
        return "\n".join(lines), buttons

    async def settings_view(self, note: str = "") -> Tuple[str, Rows]:
        a, s = self.app, self.app.s
        def tog(on: bool) -> str:
            return "✅" if on else "❌"
        head = f"{note}\n\n" if note else ""
        text = (f"{head}⚙️ إعدادات الحماية (لحد إعادة التشغيل؛ للتثبيت عدّل .env)\n{LINE}\n"
                f"🛡️ الحماية: {tog(a.moderation.enabled)}\n🤖 ردود الـAI: {tog(a.pipeline.ai_reply_enabled)}\n"
                f"🔞 حظر فوري للصور الجنسية: {tog(s.instant_block_nsfw)}\n🔍 صرامة الصور (إيحاءات): {tog(s.image_strict)}\n"
                f"🎞 فحص الفيديو: {tog(s.video_moderation)}\n✏️ فحص الرسائل المعدّلة: {tog(s.moderate_edits)}")
        def b(label, key, on):
            return (f"{tog(on)} {label}", f"s:{key}", S if on else D)
        rows: Rows = [
            [b("الحماية", "mod", a.moderation.enabled), b("ردود AI", "ai", a.pipeline.ai_reply_enabled)],
            [b("حظر فوري +18", "nsfw", s.instant_block_nsfw), b("صرامة الصور", "strict", s.image_strict)],
            [b("فحص الفيديو", "vid", s.video_moderation), b("فحص التعديلات", "edit", s.moderate_edits)],
            [("🏠 الرئيسية", "m", P)],
        ]
        return text, rows

    async def stats_view(self) -> Tuple[str, Rows]:
        a = self.app
        st = await a.db.stats()
        ps = a.pipeline.status()
        ms = a.moderation.stats
        text = (f"📊 الإحصائيات\n{LINE}\n"
                f"👥 أشخاص: {st.get('profiles', 0)} · 📨 رسائل محفوظة: {st['messages']} · 🧩 معلومات: {st['facts']} · 📝 ملخصات: {st['summaries']}\n"
                f"🚫 محظورين: {st['blocked']} · ⚠️ تحذيرات: {st['warnings']} · ⭐ VIP: {st.get('trusted', 0)}\n{LINE}\n"
                f"منذ التشغيل: تحذيرات {ms.get('warnings', 0)} · حظر {ms.get('blocks', 0)} (فوري {ms.get('instant_blocks', 0)})\n"
                f"رسائل مستلمة {ps.get('received', 0)} · ردود {ps.get('replies', 0)} · صور غير مفحوصة {ps.get('images_unchecked', 0)}")
        return text, [[("🔄 تحديث", "st", S), ("🏠 الرئيسية", "m", P)]]

    async def home(self, note: str = "") -> Tuple[str, Rows]:
        rows = await self.app.moderation.trust_list()
        lines = [f"⭐ المميزين ({len(rows)})"]
        if note:
            lines.insert(0, note + "\n")
        if rows:
            lines.append("🗑️ اسم الشخص = حذفه   |   زر النوع = تغيير النوع")
        else:
            lines.append("القائمة فارغة. اضغط ➕ لإضافة أول شخص.")
        lines.append("\nالـVIP هم الوحيدين المستثنين من التحذير والحظر. جهات الاتصال والأصدقاء المقرّبين "
                     "يتعامل وياهم بودّ أكثر بس يخضعون للحماية مثل غيرهم.")
        buttons: Rows = []
        for r in rows[:MAX_ROWS]:
            name = (r["label"] or str(r["user_id"]))[:16]
            buttons.append([(f"🗑️ {name}", f"d:{r['user_id']}", D), (_TIER_LABEL.get(r["tier"], r["tier"]), f"t:{r['user_id']}", P)])
        if len(rows) > MAX_ROWS:
            lines.append(f"(أول {MAX_ROWS} من {len(rows)} — الباقي بالأمر .vip)")
        buttons.append([("➕ إضافة شخص", "a", S), ("🔄 تحديث", "r", P)])
        buttons.append([("🏠 الرئيسية", "m", P)])
        return "\n".join(lines), buttons

    async def _unblock(self, uid: int) -> str:
        mod = self.app.moderation
        was = await mod.unblock(uid)
        await mod.db.clear_warning(uid)
        tg_ok = True
        try:
            tg_ok = await self.app.transport.unblock(uid)
        except Exception:
            tg_ok = False
        return (("🔓 انرفع الحظر" if was else "🔓 تأكدت إنه مو محظور") + f" عن {uid}"
                + (" ✅ (انفك بتيليجرام)" if tg_ok else " (⚠️ انفك بالبوت بس تيليجرام ما قبل، جرّب مرة ثانية)"))

    async def _toggle(self, key: str) -> str:
        a, s = self.app, self.app.s
        if key == "mod":
            a.moderation.enabled = not a.moderation.enabled
            return f"🛡️ الحماية {'شغالة ✅' if a.moderation.enabled else 'متوقفة ❌'}"
        if key == "ai":
            v = not a.pipeline.ai_reply_enabled
            a.presence.ai_manual_enabled = v
            a.pipeline.ai_reply_enabled = v
            if not v:
                a.presence._cancel_tracked()
            return f"🤖 ردود الـAI {'شغالة ✅' if v else 'متوقفة ❌'}"
        attr = {"nsfw": "instant_block_nsfw", "strict": "image_strict", "vid": "video_moderation",
                "edit": "moderate_edits"}.get(key)
        if attr:
            setattr(s, attr, not getattr(s, attr))
            return f"{'✅' if getattr(s, attr) else '❌'} {attr}"
        return ""

    async def callback(self, data: str) -> Tuple[str, str, Rows]:
        kind, _, arg = (data or "").partition(":")
        a, mod = self.app, self.app.moderation
        parts = arg.split(":")
        uid = int(parts[0]) if parts and parts[0].lstrip("-").isdigit() else None
        tail = parts[1] if len(parts) > 1 else ""
        page = int(tail) if tail.isdigit() else 0

        if kind == "noop":
            return "", "", []
        if kind == "m":
            self.adding = False
            t, r = await self.menu()
            return "", t, r
        if kind == "b":
            t, r = await self.blocked_view(int(arg) if arg.isdigit() else 0)
            return "", t, r
        if kind == "w":
            t, r = await self.warnings_view(int(arg) if arg.isdigit() else 0)
            return "", t, r
        if kind == "p":
            t, r = await self.people_view(int(arg) if arg.isdigit() else 0)
            return "", t, r
        if kind == "st":
            t, r = await self.stats_view()
            return "", t, r
        if kind == "i" and uid is not None:
            t, r = await self.person_view(uid, tail or "m")
            return "", t, r
        if kind == "s":
            note = await self._toggle(arg) if arg else ""
            t, r = await self.settings_view(note)
            return note, t, r
        if kind == "sm":
            note = await self._toggle(arg)
            t, r = await self.menu(note)
            return note, t, r
        if kind == "ub" and uid is not None:
            note = await self._unblock(uid)
            if tail == "i":
                t, r = await self.person_view(uid, "m", note)
            else:
                t, r = await self.blocked_view(page, note)
            return "تم ✅", t, r
        if kind == "uw" and uid is not None:
            had = await mod.clear_warning(uid)
            note = f"✅ انفك التحذير عن {uid}" if had else "ما كان عنده تحذير فعّال"
            if tail == "i":
                t, r = await self.person_view(uid, "m", note)
            else:
                t, r = await self.warnings_view(page, note)
            return "تم ✅" if had else "", t, r
        if kind == "bk" and uid is not None:
            back = "i:%d" % uid if tail == "i" else f"w:{page}"
            prof = await a.db.get_profile(uid) or {"user_id": uid}
            prof["user_id"] = uid
            return "", f"هل تريد حظر هذا الشخص؟\n\n{person_line(prof)}", [
                [("✅ نعم، احظره", f"by:{uid}:{tail}", D), ("↩️ لا", back, P)]]
        if kind == "by" and uid is not None:
            if uid == a.s.owner_id:
                return "ما أكدر أحظر حسابك", *(await self.menu())
            await mod.block(uid, "manual by owner (panel)")
            try:
                await a.transport.block(uid)
            except Exception:
                pass
            note = f"🚫 انحظر {uid}"
            if tail == "i":
                t, r = await self.person_view(uid, "m", note)
            else:
                t, r = await self.warnings_view(page, note)
            return "تم 🚫", t, r
        if kind == "vp" and uid is not None:
            was = await mod.trust_add(uid, "vip", "")
            note = f"⭐ انضاف {uid} للمميزين" + (" (وانرفع الحظر عنه)" if was else "")
            try:
                await a.transport.unblock(uid)
            except Exception:
                pass
            t, r = await self.person_view(uid, "m", note)
            return "⭐", t, r
        if kind == "fg" and uid is not None:
            prof = await a.db.get_profile(uid) or {"user_id": uid}
            prof["user_id"] = uid
            return "", (f"🧹 نسيان كامل لهذا الشخص؟\n\n{person_line(prof)}\n\n"
                        "راح ينمسح كل شي: الرسائل والمعلومات والملخص والملف والتحذيرات والحظر (ويتفك عنه) وسجل الأحداث، "
                        "وإذا كان VIP ينشال. أول رسالة منه بعدها تنعامل كأنه شخص جديد تماماً. هذا ما يرجع."), [
                [("✅ نعم، انساه نهائياً", f"fy:{uid}", D), ("↩️ لا", f"i:{uid}:m", P)]]
        if kind == "fy" and uid is not None:
            res = await a.pipeline.forget_person(uid)
            t, r = await self.menu(f"🧹 نسيت {uid} بالكامل — يرجع شخص جديد عند أول رسالة "
                                   f"(انمسح: {res.get('messages', 0)} رسالة، {res.get('facts', 0)} معلومة، "
                                   f"{res.get('events', 0)} حدث).")
            return "تم 🧹", t, r

        if kind == "r":
            self.adding = False
            text, rows = await self.home()
            return "", text, rows
        if kind == "a":
            self.adding = True
            return ("", "ادزلي الايدي أو @اليوزر (ممكن تحوّل رسالة منه) 👇\n"
                        "أو اضغط «اختيار شخص» من لوحة المفاتيح تحت.\n"
                        "ممكن تضيف النوع: مميز / صديق / حبيب.", [[("↩️ رجوع", "r", P)]])
        if kind in ("d", "y", "t") and arg.lstrip("-").isdigit():
            uid = int(arg)
            who = next((r for r in await mod.trust_list() if r["user_id"] == uid), None)
            name = (who["label"] if who else "") or str(uid)
            if kind == "d":
                return "", f"هل تريد حذف «{name}» من المميزين؟\nراح يرجع يخضع للتحذير والحظر عادي.", [
                    [("✅ نعم، احذفه", f"y:{uid}", D), ("↩️ لا", "r", P)]]
            if kind == "y":
                removed = await mod.trust_remove(uid)
                text, rows = await self.home(f"🗑️ شلت «{name}» من المميزين." if removed else "ما كان بالقائمة.")
                return "تم الحذف" if removed else "", text, rows
            if who is None:
                text, rows = await self.home()
                return "", text, rows
            new = _NEXT_TIER.get(who["tier"], "vip")
            await mod.trust_add(uid, new, who["label"])
            text, rows = await self.home()
            return _TIER_LABEL[new], text, rows
        text, rows = await self.menu()
        return "", text, rows

    async def message(self, text: str, picked: Sequence[int] = (), forwarded: Optional[int] = None
                      ) -> Optional[Tuple[str, Rows]]:
        t = (text or "").strip()
        head = t.split(" ")[0].lower()
        if head in ("/start", "/menu", "/panel", "/home") or t in ("🏠 /start", "🏠 القائمة الرئيسية", "القائمة"):
            self.adding = False
            return await self.menu()
        if head == "/vip":
            self.adding = False
            return await self.home()
        if head in ("/bans", "/blocked"):
            return await self.blocked_view(0)
        if head in ("/warns", "/warnings"):
            return await self.warnings_view(0)
        if head == "/people":
            return await self.people_view(0)
        if not (self.adding or picked or forwarded):
            return None
        if t.lower() in ("الغاء", "إلغاء", "cancel", "/cancel"):
            self.adding = False
            return await self.home("تمام، ألغيت.")
        ref, tier = _split_ref_tier(t)
        uid = next(iter(picked), None) or forwarded
        ok, msg = await self.app.commands._vip_add(ref, tier, uid=uid)
        if ok:
            self.adding = False
            return await self.home(msg)
        return msg + "\nجرّب مرة ثانية، أو اضغط 🔄 تحديث للرجوع.", [[("↩️ رجوع", "r", P)]]

    async def alert(self, info: Dict[str, Any]) -> Tuple[str, Rows]:
        uid = int(info["user_id"])
        blocked = info.get("action") == "block"
        name = info.get("name") or ""
        prof = {"user_id": uid, "first_name": name, "username": info.get("username") or ""}
        row = {"label": info.get("label"), "kind": info.get("kind")}
        title = "🚫 تم حظر شخص" if blocked else "⚠️ تم تحذير شخص"
        ev = _short(info.get("evidence") or "", 160)
        text = (f"{title}\n{LINE}\n{person_line(prof)}\n📌 السبب: {_reason_ar(row)}"
                + (f"\n🧾 الدليل: {ev}" if ev else "") + f"\n🔢 رقم المخالفة: {info.get('count') or 1}")
        first = ("🔓 رفع الحظر", f"ub:{uid}:i", S) if blocked else ("✅ فك التحذير", f"uw:{uid}:i", S)
        rows: Rows = [[first, ("🔎 تفاصيل", f"i:{uid}:m", P)], [("⭐ إضافة VIP", f"vp:{uid}", P)]]
        if not blocked:
            rows[1].append(("🚫 حظر", f"bk:{uid}:i", D))
        return text, rows


def _back_data(back: str) -> str:
    if back and back[0] in "bwp" and back[1:].isdigit():
        return f"{back[0]}:{back[1:]}"
    return "m"

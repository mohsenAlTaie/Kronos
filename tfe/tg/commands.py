from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ..ai.roles import Role
from ..logs import TELEGRAM as log
from .profile import build_time_string, rotating_bio

HELP = """أوامر المالك:
.ai on|off — ردود الـAI (الحماية تبقى شغالة)
.presence — حالة حضورك | .wake — صحّي الـAI فوراً (تجاهل حالة الاتصال)
.ai_status — حالة الـAI والموديلات
.model [role] <name> — تغيير موديل (chat|fast|reasoning|moderation|summary)
.models — الموديلات المضبوطة وتوفرها
.draft on|off — وضع المسودة
.moderation on|off — الحماية
.smart_presence on|off — الحضور الذكي
.memory [user_id] — إحصائيات الذاكرة أو ذاكرة شخص
.forget <user_id> — نسيان كامل لشخص (ذاكرة + ملف + تحذيرات + حظر + سجل، يرجع كأنه جديد)
.clear [all] — مسح الذاكرة (all = مع التحذيرات والحظر المحلي)
.cleanup — تنظيف
.block <id> / .unblock <id> / .warnings
.bans — المحظورين (الاسم/اليوزر/الايدي/السبب) | .warns — التحذيرات الفعّالة | .unwarn <id> — فك التحذير | .who <id> — ملف شخص
.terms — كلمات مسيئة تعلّمها البوت | .term add|ok|del <عبارة>
.stickers — ستيكراتك اللي يستعملها البوت | .stickers refresh | .stickers 😂 (يرسل تجربة هنا)
.vip — قائمة المميزين (يحجي وياهم بس ما يحذّرهم ولا يحظرهم أبداً) | .vip add <ايدي|@يوزر> [مميز|صديق|حبيب] | .vip del <ايدي> | .vip here
.send <id> <text>
.search <query> / .search_provider <name>
.time / .bio / .status / .health / .stats"""

_ROLES = {r.value: r for r in Role}
_CMD = re.compile(r"^\.(\w+)(?:\s+(.*))?$", re.S)


def _onoff(v: str) -> Optional[bool]:
    v = (v or "").strip().lower()
    return True if v == "on" else False if v == "off" else None


def _flag(b: bool) -> str:
    return "مفعّل ✅" if b else "معطّل ❌"


_TIER_WORDS = {
    "vip": "vip", "مميز": "vip", "مميزين": "vip",
    "friend": "friend", "صديق": "friend", "اصدقاء": "friend", "أصدقاء": "friend", "رفيق": "friend",
    "love": "love", "حبيب": "love", "احباب": "love", "أحباب": "love", "عزيز": "love", "حبايب": "love",
}
_TIER_LABEL = {"vip": "⭐ مميز", "friend": "🤝 صديق", "love": "💜 حبيب"}
_CANCEL = {"الغاء", "إلغاء", "cancel", "لا", "stop"}
VIP_PROMPT = ("اكتب ايدي العضو المستثنى من الحظر وادزله 👇\n"
              "(ممكن تضيف النوع: مميز / صديق / حبيب — مثل: 123456789 صديق)\n"
              "🗑️ للحذف: «حذف 2» (برقمه بالقائمة) أو «حذف» وبعده الايدي\n"
              "🔄 لتغيير النوع: «نوع 2 صديق»\n"
              "🧹 للخروج: «الغاء»")


def _split_ref_tier(arg: str) -> Tuple[str, str]:
    ref, tier = "", "vip"
    for tok in (arg or "").split():
        w = tok.strip().lower()
        if w in _TIER_WORDS:
            tier = _TIER_WORDS[w]
        elif not ref:
            ref = tok.strip()
    return ref, tier


_DEL_WORDS = {"حذف", "شيل", "ازالة", "إزالة", "امسح", "del", "remove", "delete"}
_TYPE_WORDS = {"نوع", "غير", "type", "tier"}
_ADD_WORDS = {"اضافة", "إضافة", "اضف", "أضف", "add"}


class OwnerCommands:
    def __init__(self, app: Any):
        self.app = app
        self._pending: Dict[int, float] = {}

    def _table(self):
        return {
            "help": self.c_help, "ai": self.c_ai, "presence": self.c_presence, "wake": self.c_wake, "ai_status": self.c_ai_status,
            "model": self.c_model, "models": self.c_models, "draft": self.c_draft, "moderation": self.c_moderation,
            "smart_presence": self.c_smart, "memory": self.c_memory, "forget": self.c_forget, "clear": self.c_clear,
            "bans": self.c_bans, "warns": self.c_warns, "unwarn": self.c_unwarn, "who": self.c_who,
            "cleanup": self.c_cleanup, "block": self.c_block, "unblock": self.c_unblock, "warnings": self.c_warnings,
            "send": self.c_send, "search": self.c_search, "search_provider": self.c_search_provider,
            "time": self.c_time, "bio": self.c_bio, "status": self.c_status, "health": self.c_health,
            "stats": self.c_stats, "terms": self.c_terms, "term": self.c_term,
            "vip": self.c_vip, "friends": self.c_vip, "trust": self.c_vip,
            "stickers": self.c_stickers, "sticker": self.c_stickers,
        }

    def is_command(self, text: str) -> bool:
        m = _CMD.match((text or "").strip())
        return bool(m) and m.group(1).lower() in self._table()

    async def run(self, text: str, sender_id: int, chat_id: Optional[int] = None) -> Optional[List[str]]:
        if sender_id != self.app.s.owner_id:
            return None
        m = _CMD.match((text or "").strip())
        fn = self._table().get(m.group(1).lower()) if m else None
        if fn is None:
            return None
        try:
            arg = (m.group(2) or "").strip()
            out = await (fn(arg, chat_id) if m.group(1).lower() in ("vip", "friends", "trust", "stickers", "sticker")
                         else fn(arg))
        except Exception as e:
            log.warning(f"command .{m.group(1)} failed: {type(e).__name__}")
            out = [f"❌ فشل الأمر: {type(e).__name__}"]
        return out if isinstance(out, list) else [out]

    async def feed(self, text: str, sender_id: int, chat_id: int) -> Optional[List[str]]:
        if sender_id != self.app.s.owner_id or not self._pending:
            return None
        until = self._pending.get(chat_id)
        if until is None:
            return None
        if time.monotonic() > until:
            self._pending.pop(chat_id, None)
            return None
        t = (text or "").strip()
        if not t or t.startswith("."):
            return None
        if t.lower() in _CANCEL:
            self._pending.pop(chat_id, None)
            return ["تمام، طلعت من قائمة المميزين ✅"]
        words = t.split(None, 1)
        head, rest = words[0].lower(), (words[1] if len(words) > 1 else "")
        self._pending[chat_id] = time.monotonic() + 120
        if head in _DEL_WORDS:
            return await self._vip_delete(rest)
        if head in _TYPE_WORDS:
            return await self._vip_retype(rest)
        if head in _ADD_WORDS:
            t = rest
        ref, tier = _split_ref_tier(t)
        ok, msg = await self._vip_add(ref, tier)
        if ok:
            return [msg, await self._vip_list()]
        return [msg + "\nاكتب الايدي مرة ثانية، أو «حذف 2»، أو «الغاء»."]

    async def c_help(self, arg):
        return HELP

    async def c_ai(self, arg):
        v = _onoff(arg)
        if v is None:
            return f"🤖 ردود الـAI: {_flag(self.app.presence.ai_manual_enabled)}\nاستخدم: .ai on | .ai off"
        self.app.presence.ai_manual_enabled = v
        self.app.pipeline.ai_reply_enabled = v
        if not v:
            self.app.presence._cancel_tracked()
        return f"🤖 ردود الـAI: {_flag(v)}\n🛡️ الحماية مستقلة وما تتأثر: {_flag(self.app.moderation.enabled)}"

    async def c_presence(self, arg):
        p = self.app.presence.snapshot()
        seen = p["seconds_since_last_seen"]
        return (f"👤 الحضور الذكي: {_flag(p['enabled'])}\nالحالة: {p['state']} (owner_online={p['owner_online']})\n"
                f"AI مسموح الآن: {'نعم ✅' if p['ai_allowed'] else 'لا ⏸️'}\n"
                f"مهلة الرجوع المتبقية: {p['grace_remaining']:.0f}ث\n"
                f"آخر إشارة منك: {'—' if seen is None else f'{seen:.0f}ث'}\n"
                f"يصحى بعد: {p['wake_in']:.0f}ث | حالة الاتصال: "
                + (f"متصل (تنتهي بعد {p['status_expires_in']:.0f}ث)" if p["status_online"] and p["status_expires_in"] is not None
                   else "متصل" if p["status_online"] else "غير متصل/مجهول") + "\n"
                f"توليدات ملغاة: {p['cancelled_generations']}\n"
                + ("آخر الإشارات: " + " ، ".join(f"{src} قبل {age:.0f}ث" for age, src in p["recent_signals"][:6])
                   if p["recent_signals"] else "آخر الإشارات: لا شي وصل لحد الآن"))

    async def c_wake(self, arg):
        await self.app.presence.force_offline("owner command")
        p = self.app.presence.snapshot()
        return (f"☀️ صحّيت الـAI: ما عاد يعتبرك موجود.\nالحالة: {p['state']} | AI مسموح الآن: "
                f"{'نعم ✅' if p['ai_allowed'] else 'لا ⏸️ (أوقفته بـ .ai off)'}\n"
                "إذا سويت أي شي بالحساب (قراءة/كتابة) ينام من جديد، وهذا طبيعي.")

    async def c_ai_status(self, arg):
        r = self.app.router.status()
        on = self.app.pipeline.ai_reply_enabled and self.app.presence.ai_manual_enabled
        lines = [f"🤖 AI: {_flag(on)} (مسموح الآن: {'نعم' if self.app.presence.ai_allowed(None) else 'لا'})"]
        for role, chain in r["chains"].items():
            lines.append(f"• {role}: {' → '.join(chain) or '—'}")
        lines.append(f"🔌 دوائر مفتوحة: {', '.join(m for m, b in r['breakers'].items() if b['open']) or 'لا يوجد'}")
        lines.append(f"📊 {r['stats']}")
        return "\n".join(lines)

    async def c_model(self, arg):
        r = self.app.router
        if not arg:
            return "\n".join(f"{role}: {' → '.join(chain)}" for role, chain in r.status()["chains"].items())
        parts = arg.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in _ROLES:
            role, name = _ROLES[parts[0].lower()], parts[1].strip()
        else:
            role, name = Role.CHAT, arg.strip()
        r.set_model(role, name)
        return f"✅ {role.value} ← {name}\n(لحد إعادة التشغيل؛ للتثبيت عدّل .env)"

    async def c_models(self, arg):
        rows = self.app.router.availability()
        return "📚 الموديلات المضبوطة:\n" + "\n".join(f"{'✅' if x['ok'] else '⚠️'} {x['model']} — {x['note']}" for x in rows)

    async def c_draft(self, arg):
        v = _onoff(arg)
        if v is None:
            return f"📝 وضع المسودة: {_flag(self.app.pipeline.draft_mode)}"
        self.app.pipeline.draft_mode = v
        return f"📝 وضع المسودة: {_flag(v)}"

    async def c_moderation(self, arg):
        v = _onoff(arg)
        if v is None:
            return f"🛡️ الحماية: {_flag(self.app.moderation.enabled)}"
        self.app.moderation.enabled = v
        return f"🛡️ الحماية: {_flag(v)}"

    async def c_smart(self, arg):
        v = _onoff(arg)
        if v is None:
            return f"👤 الحضور الذكي: {_flag(self.app.presence.enabled)}"
        await self.app.presence.set_enabled(v)
        if v:
            await self.app.feed.probe_once()
        return f"👤 الحضور الذكي: {_flag(v)}"

    async def c_memory(self, arg):
        db = self.app.db
        if arg.strip().lstrip("-").isdigit():
            uid = int(arg)
            o = await db.user_overview(uid)
            facts = "\n".join(f"• {f['fact']}" for f in o["facts"][:15]) or "—"
            pr = await db.get_profile(uid) or {}
            who = (" ".join(x for x in (pr.get("first_name"), pr.get("last_name")) if x) or "—") + (f" @{pr['username']}" if pr.get("username") else "")
            return (f"🧠 ذاكرة {uid} — {who}\nرسائل: {o['messages']}\nمحظور: {'نعم' if o['blocked'] else 'لا'}\n"
                    f"الملخص: {o['summary'] or '—'}\nالمعلومات:\n{facts}")
        st = await db.stats()
        s = self.app.s
        return (f"🧠 حالة الذاكرة\n📨 رسائل: {st['messages']}\n👥 أشخاص: {st['users']}\n🧩 معلومات: {st['facts']}\n"
                f"📝 ملخصات: {st['summaries']}\n🧹 TTL الرسائل {s.memory_ttl_days} يوم | المعلومات {s.fact_ttl_days} يوم\n"
                f"📊 التلخيص: {_flag(s.enable_summarization)}")

    async def c_forget(self, arg):
        if not arg.strip().isdigit():
            return "الاستخدام: .forget <user_id>"
        res = await self.app.pipeline.forget_person(int(arg))
        return (f"🧹 نسيت {arg} بالكامل: {res}\n(كل شي انمسح: ذاكرة، ملف، تحذيرات، حظر، VIP، سجل — "
                "وأول رسالة منه تنعامل كأنه شخص جديد)")

    async def c_clear(self, arg):
        await self.app.db.clear_all_memory()
        if arg.strip().lower() == "all":
            for row in await self.app.db.list_blocked():
                await self.app.moderation.unblock(row["user_id"])
            for row in await self.app.db.list_warnings():
                await self.app.db.unblock(row["user_id"])
            return "🧹 تم مسح الذاكرة + التحذيرات + الحظر المحلي (حظر تيليجرام يبقى إلا تسوي .unblock)."
        return "🧹 تم مسح الذاكرة. التحذيرات والحظر ما انمسحت."

    async def c_cleanup(self, arg):
        return f"✅ تنظيف: {await self.app.cleanup()}"

    async def c_block(self, arg):
        if not arg.strip().isdigit():
            return "الاستخدام: .block <user_id>"
        uid = int(arg)
        if uid == self.app.s.owner_id:
            return "❌ ما أكدر أحظر حسابك."
        await self.app.moderation.block(uid, "manual by owner")
        ok = await self.app.transport.block(uid)
        return f"🚫 تم حظر {uid} (DB ✅ | تيليجرام {'✅' if ok else '⚠️'})"

    async def c_unblock(self, arg):
        if not arg.strip().isdigit():
            return "الاستخدام: .unblock <user_id>"
        uid = int(arg)
        await self.app.moderation.unblock(uid)
        ok = await self.app.transport.unblock(uid)
        return f"✅ تم رفع الحظر عن {uid} (تيليجرام {'✅' if ok else '⚠️'})"

    async def c_warnings(self, arg):
        ws, bs = await self.app.db.list_warnings(), await self.app.db.list_blocked()
        ttl, now = self.app.s.warning_ttl_hours * 3600, time.time()
        live = [w for w in ws if now - w["last_ts"] <= ttl]
        out = ["⚠️ تحذيرات فعّالة:"] + [f"• {w['user_id']} → {w['count']} ({w['reason'] or '-'})" for w in live[:20]] \
            if live else ["✅ لا يوجد تحذيرات فعّالة"]
        if bs:
            out.append("🚫 محظورين:")
            out += [f"• {b['user_id']} — {b['label'] or ''} {(b['reason'] or '')[:40]}" for b in bs[:20]]
        return "\n".join(out)

    async def c_bans(self, arg):
        from ..control import ago, person_name, _reason_ar
        db = self.app.db
        page = int(arg) - 1 if arg.strip().isdigit() and int(arg) > 0 else 0
        total = await db.blocked_count()
        if not total:
            return "✅ ما كو أحد محظور"
        rows = await db.blocked_page(page * 10, 10)
        out = [f"🚫 المحظورين ({total}) — صفحة {page + 1}"]
        for b in rows:
            ev = (b.get("evidence") or "")[:80]
            out.append(f"• {person_name(b)} {('@' + b['username']) if b.get('username') else ''} | {b['user_id']}\n"
                       f"  {_reason_ar(b)} — {ago(b.get('blocked_at'))}" + (f"\n  🧾 {ev}" if ev else ""))
        out.append("لرفع الحظر: .unblock <id>" + (f" | الصفحة التالية: .bans {page + 2}" if total > (page + 1) * 10 else ""))
        return "\n".join(out)

    async def c_warns(self, arg):
        from ..control import ago, person_name, _reason_ar
        rows = await self.app.db.warnings_page(self.app.s.warning_ttl_hours, 0, 20)
        if not rows:
            return "✅ لا يوجد تحذيرات فعّالة"
        out = [f"⚠️ التحذيرات الفعّالة ({len(rows)})"]
        for w in rows:
            out.append(f"• {person_name(w)} {('@' + w['username']) if w.get('username') else ''} | {w['user_id']} — "
                       f"{_reason_ar(w)} ×{w['count']} {ago(w.get('last_ts'))}")
        out.append("لفك تحذير: .unwarn <id>")
        return "\n".join(out)

    async def c_unwarn(self, arg):
        if not arg.strip().isdigit():
            return "الاستخدام: .unwarn <user_id>"
        had = await self.app.moderation.clear_warning(int(arg))
        return f"✅ انفك التحذير عن {arg}" if had else "ما عنده تحذير فعّال"

    async def c_who(self, arg):
        if not arg.strip().isdigit():
            return "الاستخدام: .who <user_id>"
        from ..control import ControlPanel
        text, _ = await ControlPanel(self.app).person_view(int(arg))
        return text

    async def c_terms(self, arg):
        rows = await self.app.moderation.list_terms()
        if not rows:
            return "📚 ما تعلّمت أي عبارة جديدة لحد الآن"
        icon = {"active": "✅", "pending": "⏳", "rejected": "🚫"}
        return "📚 عبارات مسيئة متعلَّمة:\n" + "\n".join(
            f"{icon.get(r['status'], '•')} {r['term']} — {r['label']} ×{r['hits']}" for r in rows)

    async def c_term(self, arg):
        m = re.match(r"^(add|ok|del)\s+(.+)$", (arg or "").strip(), re.S | re.I)
        if not m:
            return "الاستخدام: .term add <عبارة> (تفعيل فوري) | .term ok <عبارة> (اعتماد المتعلَّمة) | .term del <عبارة> (إلغاء ومنع إعادة تعلّمها)"
        op, term = m.group(1).lower(), m.group(2).strip()
        mod = self.app.moderation
        ok = await (mod.add_term(term) if op == "add" else mod.approve_term(term) if op == "ok" else mod.reject_term(term))
        return ("✅ تم" if ok else "⚠️ ما تغيّر شي") + f": {op} «{term}»"

    async def c_stickers(self, arg, chat_id=None):
        lib = self.app.transport.stickers
        a = (arg or "").strip()
        if a.lower() in ("refresh", "reload", "تحديث"):
            idx = await lib.refresh()
            st = lib.stats()
            if not st["loaded"] or not st.get("stickers"):
                return "⚠️ ما كدرت أقرأ ستيكراتك (فاضية أو تليجرام رفض). جرّب بعد شوي."
            return (f"🔄 قريت ستيكراتك: {st['stickers']} ستيكر من {st['sets_loaded']}/{st['sets_total']} مجموعة "
                    f"+ {st['favourites']} مفضّل + {st['recent']} أخير." + (" (جزئي: FloodWait)" if st["partial"] else ""))
        if a:
            if chat_id is None:
                return "❌ اكتب الأمر داخل محادثة."
            ok = await self.app.transport.send_sticker(chat_id, a.split()[0])
            return "✅ ارسلته" if ok else "❌ ما لكيت ستيكر مناسب أو فشل الارسال (شوف .stickers)."
        lib.warm()
        st = lib.stats()
        mode = {"mine": "ستيكراتك أنت فقط", "any": "ستيكراتك ثم بحث تليجرام", "search": "بحث تليجرام العام (القديم)"}.get(
            st["source"], st["source"])
        if not st["loaded"]:
            return f"⏳ ما انقرت ستيكراتك بعد (يقراها بالخلفية). المصدر: {mode}\nجرّب .stickers refresh"
        em = " ".join(lib.top_emojis(24)) or "—"
        return (f"🎭 ستيكراتك: {st['stickers']} ستيكر | {st['sets_loaded']}/{st['sets_total']} مجموعة | "
                f"{st['favourites']} مفضّل | {st['recent']} أخير\nالمصدر: {mode}\nالإيموجيات المتوفرة: {em}")

    async def _vip_add(self, ref: str, tier: str, uid: Optional[int] = None):
        t = self.app.transport
        name = ""
        if uid is None:
            if not ref:
                return False, "❌ ما وصلني ايدي أو يوزر."
            resolved = await t.resolve_user(ref)
            if resolved is None:
                return False, f"❌ ما لكيت شخص بهذا المرجع «{ref}» (لازم يكون شخص مو بوت أو قناة)."
            uid, name = resolved
        if uid == self.app.s.owner_id:
            return False, "❌ هذا حسابك انت."
        was_blocked = await self.app.moderation.trust_add(uid, tier, name)
        unblocked = await t.unblock(uid)
        who = f"{name} ({uid})" if name else f"{uid}"
        msg = (f"✅ تمت إضافة {who} إلى قائمة المميزين كـ{_TIER_LABEL[tier]}.\n"
               "أحجي وياه عادي، وما راح أحذّره أو أحظره أبداً حتى لو غلط.")
        if was_blocked:
            msg += f"\n🔓 كان محظور، ورفعت الحظر عنه (تيليجرام {'✅' if unblocked else '⚠️'})."
        return True, msg

    async def _vip_rows(self):
        return await self.app.moderation.trust_list()

    async def _vip_target(self, ref: str, rows) -> Optional[Tuple[int, str]]:
        ref = (ref or "").strip()
        if not ref:
            return None
        if ref.isdigit() and len(ref) <= 3 and 1 <= int(ref) <= len(rows):
            r = rows[int(ref) - 1]
            return r["user_id"], r["label"]
        if ref.isdigit():
            hit = next((r for r in rows if r["user_id"] == int(ref)), None)
            if hit:
                return hit["user_id"], hit["label"]
        return await self.app.transport.resolve_user(ref)

    async def _vip_delete(self, ref: str) -> List[str]:
        rows = await self._vip_rows()
        target = await self._vip_target(ref, rows)
        if target is None:
            return ["الاستخدام: «حذف 2» (برقمه بالقائمة) أو «حذف» وبعده الايدي أو @اليوزر", await self._vip_list()]
        uid, name = target
        removed = await self.app.moderation.trust_remove(uid)
        head = (f"🗑️ شلت {name or uid} من قائمة المميزين، ورجع يخضع للحماية عادي." if removed
                else f"ما كان {name or uid} بالقائمة أصلاً.")
        return [head, await self._vip_list()]

    async def _vip_retype(self, arg: str) -> List[str]:
        ref, tier = _split_ref_tier(arg)
        rows = await self._vip_rows()
        target = await self._vip_target(ref, rows)
        if target is None or not any(r["user_id"] == target[0] for r in rows):
            return ["الاستخدام: «نوع 2 صديق» (مميز / صديق / حبيب)", await self._vip_list()]
        uid, name = target
        await self.app.moderation.trust_add(uid, tier, name)
        return [f"🔄 صار {name or uid} {_TIER_LABEL[tier]}.", await self._vip_list()]

    async def _vip_list(self) -> str:
        rows = await self._vip_rows()
        s = self.app.s
        auto = ("ℹ️ الـVIP هم الوحيدين المستثنين من التحذير والحظر. جهات الاتصال واللي تحجي وياهم بنفسك "
                "يتعامل وياهم بودّ أكثر بس يخضعون للحماية مثل غيرهم.")
        if not rows:
            return "⭐ قائمة المميزين فارغة.\n" + auto
        lines = [f"⭐ المميزين ({len(rows)}):"]
        for n, r in enumerate(rows[:40], 1):
            lines.append(f"{n}) {r['label'] or '—'} — {r['user_id']} — {_TIER_LABEL.get(r['tier'], r['tier'])}")
        lines.append("\n🗑️ للحذف: .vip del <الرقم>   🔄 للنوع: .vip type <الرقم> <صديق|حبيب|مميز>")
        return "\n".join(lines) + "\n" + auto

    async def c_vip(self, arg, chat_id=None):
        a = (arg or "").strip()
        low = a.lower()
        owner_chat = chat_id is not None and chat_id == self.app.s.owner_id
        if not a:
            if owner_chat:
                self._pending[chat_id] = time.monotonic() + 120
                return [await self._vip_list(), VIP_PROMPT]
            return [await self._vip_list(), "للإدارة التفاعلية اكتب .vip داخل «الرسائل المحفوظة»، أو .vip here هنا."]
        if low in ("list", "قائمة", "القائمة"):
            return await self._vip_list()
        head, _, rest = a.partition(" ")
        head = head.lower()
        if head in ("here", "هنا"):
            if chat_id is None or chat_id <= 0 or chat_id == self.app.s.owner_id:
                return "❌ `.vip here` تشتغل بس داخل محادثة خاصة مع الشخص."
            _, tier = _split_ref_tier(rest)
            ok, msg = await self._vip_add("", tier, uid=chat_id)
            return msg
        if head in _ADD_WORDS:
            ref, tier = _split_ref_tier(rest)
            ok, msg = await self._vip_add(ref, tier)
            return msg
        if head in _DEL_WORDS:
            return await self._vip_delete(rest)
        if head in _TYPE_WORDS:
            return await self._vip_retype(rest)
        ref, tier = _split_ref_tier(a)
        ok, msg = await self._vip_add(ref, tier)
        return msg

    async def c_send(self, arg):
        m = re.match(r"^(\d+)\s+(.+)$", arg, re.S)
        if not m:
            return "الاستخدام: .send <user_id> <text>"
        return "✅ تم الإرسال" if await self.app.transport.send(int(m.group(1)), m.group(2), None) else "❌ فشل الإرسال"

    async def c_search(self, arg):
        if not arg:
            return "الاستخدام: .search <query>"
        res = await self.app.search.search(arg)
        return f"⚠️ البحث ما نجح ({res.error})" if not res.ok else "🌐 نتائج البحث:\n" + res.text[:3500]

    async def c_search_provider(self, arg):
        if not arg:
            return f"🌐 مزود البحث: {self.app.search.provider}"
        self.app.search.provider = arg.strip()
        return f"✅ مزود البحث: {arg.strip()}"

    async def c_time(self, arg):
        s = self.app.s
        now = datetime.now(ZoneInfo(s.tz))
        return f"🕐 الوقت: {build_time_string(s.tz, s.time_format)}\n📍 {s.tz}\n📅 {now.strftime('%Y-%m-%d')}"

    async def c_bio(self, arg):
        s = self.app.s
        return (f"✨ الحالي:\n{self.app.profile.current_bio() or '—'}\n\n🔄 القادم:\n"
                f"{rotating_bio(s.tz, s.bio_bots, s.bio_interval_minutes)}\n⏰ كل {s.bio_interval_minutes} دقيقة")

    async def c_status(self, arg):
        s, a = self.app.s, self.app
        st = await a.db.stats()
        return (f"👤 {a.profile.me_first_name} (@{a.profile.me_username})\n🤖 chat: {a.router.primary_for(Role.CHAT)}\n"
                f"🌐 البحث: {a.search.provider} ({_flag(a.search.enabled)})\n📝 المسودة: {_flag(a.pipeline.draft_mode)}\n"
                f"🛡️ الحماية: {_flag(a.moderation.enabled)} (تحذير 1 → حظر {s.max_warnings})\n"
                f"🚫 محظور: {st['blocked']} | ⚠️ تحذيرات: {st['warnings']}\n"
                f"👤 الحضور: {_flag(a.presence.enabled)} — {a.presence.state.value}\n⚡ FAST_REPLY: {_flag(s.fast_reply)}")

    async def c_health(self, arg):
        a = self.app
        checks = {"telegram": bool(a.client.is_connected()), "database": await a.db.integrity_ok(),
                  "scheduler": bool(a.scheduler.running), "presence": a.presence.state.value != "unknown",
                  "ai_chain": bool(a.router.chain(Role.CHAT))}
        r = a.router.status()["stats"]
        lines = [f"{'✅' if v else '❌'} {k}" for k, v in checks.items()]
        lines.append(f"🔎 بحث: فشل متتالي {a.search.failures}")
        lines.append(f"🤖 استدعاءات {r['calls']} | نجاح {r['ok']} | فشل {r['failed']} | fallback {r['fallbacks']}")
        return "\n".join(lines)

    async def c_stats(self, arg):
        a = self.app
        return (f"📊 pipeline: {a.pipeline.status()}\n🛡️ moderation: {a.moderation.moderator.stats} | "
                f"{a.moderation.stats}\n🧠 db: {await a.db.stats()}")

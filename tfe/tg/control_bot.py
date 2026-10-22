from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from ..control import ControlPanel, Rows
from ..logs import ERROR as err_log, TELEGRAM as log


def to_buttons(rows: Rows, Button: Any) -> list:
    return [[Button.inline(b[0], data=b[1].encode()) for b in row] for row in rows]


def to_markup(rows: Rows, styles: bool = True) -> Dict[str, Any]:
    kb = []
    for row in rows:
        out = []
        for b in row:
            btn: Dict[str, Any] = {"text": b[0][:60], "callback_data": b[1][:64]}
            if styles and len(b) > 2 and b[2]:
                btn["style"] = b[2]
            out.append(btn)
        kb.append(out)
    return {"inline_keyboard": kb}


START_BUTTON = "🏠 /start"
BOT_COMMANDS = [("start", "🏠 القائمة الرئيسية"), ("bans", "🚫 المحظورين"), ("warns", "⚠️ التحذيرات"),
                ("people", "🧠 الأشخاص المحفوظين"), ("vip", "⭐ المميزين")]


class BotApi:

    def __init__(self, token: str, styles: bool = True, timeout: float = 15.0):
        self.token, self.styles, self.timeout = token, styles, timeout
        self.ok_styles = True

    def _post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}",
                                     data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode() or "{}")
            except Exception:
                return {"ok": False, "description": f"HTTP {e.code}"}

    async def call(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(self._post, method, payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return {"ok": False, "description": type(e).__name__}

    async def _with_markup(self, method: str, payload: Dict[str, Any], rows: Rows) -> Dict[str, Any]:
        payload = {**payload, "text": payload["text"][:4000]}
        res = await self.call(method, {**payload, "reply_markup": to_markup(rows, self.styles and self.ok_styles)})
        if not res.get("ok") and self.styles and self.ok_styles and "not modified" not in str(res.get("description", "")):
            desc = str(res.get("description", "")).lower()
            if "style" in desc or "button" in desc or "unknown" in desc or "can't parse" in desc:
                self.ok_styles = False
                res = await self.call(method, {**payload, "reply_markup": to_markup(rows, False)})
        return res

    async def send_keyboard(self, chat_id: int, text: str = "⌨️ زر القائمة جاهز تحت 👇") -> bool:
        res = await self.call("sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": {
            "keyboard": [[{"text": START_BUTTON}]], "resize_keyboard": True, "is_persistent": True}})
        return bool(res.get("ok"))

    async def set_commands(self) -> bool:
        res = await self.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in BOT_COMMANDS]})
        await self.call("setChatMenuButton", {"menu_button": {"type": "commands"}})
        return bool(res.get("ok"))

    async def send(self, chat_id: int, text: str, rows: Rows) -> bool:
        return bool((await self._with_markup("sendMessage", {"chat_id": chat_id, "text": text}, rows)).get("ok"))

    async def edit(self, chat_id: int, message_id: int, text: str, rows: Rows) -> bool:
        res = await self._with_markup("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text}, rows)
        return bool(res.get("ok")) or "not modified" in str(res.get("description", ""))


def picker_keyboard(types: Any) -> Optional[Any]:
    btn_cls, kind_cls = getattr(types, "KeyboardButtonRequestPeer", None), getattr(types, "RequestPeerTypeUser", None)
    if btn_cls is None or kind_cls is None:
        return None
    try:
        btn = btn_cls(text="👤 اختيار شخص", button_id=1, peer_type=kind_cls(bot=False), max_quantity=1)
        return types.ReplyKeyboardMarkup(rows=[types.KeyboardButtonRow(buttons=[btn])], resize=True, single_use=True)
    except Exception:
        return None


def _picked_ids(message: Any) -> List[int]:
    action = getattr(message, "action", None)
    ids = []
    for p in getattr(action, "peers", None) or []:
        uid = getattr(p, "user_id", None)
        if isinstance(uid, int):
            ids.append(uid)
    return ids


def _forwarded_id(message: Any) -> Optional[int]:
    fwd = getattr(message, "fwd_from", None)
    uid = getattr(getattr(fwd, "from_id", None), "user_id", None)
    return uid if isinstance(uid, int) else None


def register_control_handlers(bot: Any, app: Any, panel: ControlPanel, Button: Any, events: Any, types: Any,
                              api: Optional[BotApi] = None) -> None:
    owner = app.s.owner_id
    keyboard = picker_keyboard(types)

    async def show(text: str, rows: Rows, *, message_id: Optional[int] = None, event: Any = None) -> None:
        if api is not None:
            ok = await (api.edit(owner, message_id, text, rows) if message_id else api.send(owner, text, rows))
            if ok:
                return
        if message_id and event is not None:
            await event.edit(text, buttons=to_buttons(rows, Button))
        else:
            await bot.send_message(owner, text, buttons=to_buttons(rows, Button))

    state = {"kb": False}

    async def ensure_keyboard() -> None:
        if state["kb"]:
            return
        state["kb"] = True
        ok = await api.send_keyboard(owner) if api is not None else False
        if not ok:
            try:
                await bot.send_message(owner, "⌨️ زر القائمة جاهز تحت 👇", buttons=[[Button.text(START_BUTTON, resize=True)]])
            except Exception:
                state["kb"] = False

    async def alert(info: Dict[str, Any]) -> None:
        text, rows = await panel.alert(info)
        await show(text, rows)

    if getattr(app, "pipeline", None) is not None:
        app.pipeline.on_moderation = alert

    @bot.on(events.NewMessage(incoming=True))
    async def _on_message(event):
        try:
            if event.sender_id != owner:
                return
            msg = event.message
            out = await panel.message(event.raw_text or "", _picked_ids(msg), _forwarded_id(msg))
            if out is None:
                return
            text, rows = out
            await ensure_keyboard()
            await show(text, rows)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.error(f"control bot message error: {type(e).__name__}: {e}")

    @bot.on(events.CallbackQuery)
    async def _on_press(event):
        try:
            if event.sender_id != owner:
                await event.answer("هذي اللوحة لصاحب الحساب فقط.", alert=True)
                return
            data = (event.data or b"").decode("utf-8", "ignore")
            toast, text, rows = await panel.callback(data)
            if text:
                await show(text, rows, message_id=getattr(event, "message_id", None) or getattr(event.query, "msg_id", None),
                           event=event)
            if data == "a" and keyboard is not None:
                await bot.send_message(owner, "اختار الشخص من الزر 👇", buttons=keyboard)
                state["kb"] = False
            await event.answer(toast or None)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.error(f"control bot button error: {type(e).__name__}: {e}")


async def start_control_bot(app: Any) -> Optional[Any]:
    token = app.s.control_bot_token
    if not token:
        return None
    try:
        from telethon import Button, TelegramClient, events, types
        from telethon.sessions import StringSession
        bot = TelegramClient(StringSession(), app.s.api_id, app.s.api_hash)
        await bot.start(bot_token=token)
        api = BotApi(token, styles=bool(getattr(app.s, "button_styles", True)))
        register_control_handlers(bot, app, ControlPanel(app), Button, events, types, api)
        try:
            await api.set_commands()
        except Exception:
            pass
        me = await bot.get_me()
        log.info(f"control bot ready: @{getattr(me, 'username', '?')} — open it and send /start")
        return bot
    except asyncio.CancelledError:
        raise
    except Exception as e:
        err_log.warning(f"control bot not started ({type(e).__name__}: {e})")
        return None

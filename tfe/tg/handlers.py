from __future__ import annotations

import asyncio
import time
from typing import Any, Optional, Set, Tuple

from telethon import events, functions, types

from ..logs import ERROR as err_log, PRESENCE as log, TELEGRAM as tg_log
from ..pipeline import Incoming

_ONLINE = types.UserStatusOnline
_OFFLINE_TYPES = tuple(getattr(types, n) for n in (
    "UserStatusOffline", "UserStatusRecently", "UserStatusLastWeek", "UserStatusLastMonth") if hasattr(types, n))


_ACTIVITY_NAMES = ("UpdateReadHistoryInbox", "UpdateReadChannelInbox", "UpdateDraftMessage", "UpdateDialogUnreadMark",
                   "UpdateReadMessagesContents", "UpdateChannelReadMessagesContents")
_ACTIVITY_TYPES = tuple(getattr(types, n) for n in _ACTIVITY_NAMES if hasattr(types, n))


def _peer_chat_id(update: Any) -> Optional[int]:
    peer = getattr(update, "peer", None)
    peer = getattr(peer, "peer", peer)
    uid = getattr(peer, "user_id", None)
    return int(uid) if uid else None


async def note_activity_update(app: Any, update: Any) -> bool:
    if app.transport.own_send_recent():
        return False
    name = type(update).__name__.replace("Update", "", 1)
    chat = _peer_chat_id(update)
    await app.presence.note_owner_message(chat, source=f"activity:{name}")
    if chat:
        try:
            app.pipeline.discard_held(chat)
        except AttributeError:
            pass
    return True


async def quote_of(event: Any) -> Tuple[str, bool]:
    if not getattr(getattr(event, "message", None), "is_reply", False):
        return "", False
    try:
        rm = await asyncio.wait_for(event.get_reply_message(), timeout=3.0)
    except asyncio.CancelledError:
        raise
    except Exception:
        return "", False
    if rm is None:
        return "", False
    return (getattr(rm, "raw_text", "") or "").strip()[:200], bool(getattr(rm, "out", False))


def looks_like_bot(sender: Any, msg: Any, user_id: int) -> bool:
    if getattr(sender, "bot", False):
        return True
    if user_id == 777000:
        return True
    if getattr(msg, "via_bot_id", None) or getattr(msg, "via_bot", None):
        return True
    return getattr(msg, "reply_markup", None) is not None


class PresenceFeed:
    def __init__(self, app: Any):
        self.app = app
        self._task: Optional[asyncio.Task] = None
        self.min_interval = 5.0

    async def _apply_status(self, status: Any, source: str) -> Optional[bool]:
        p = self.app.presence
        if isinstance(status, _ONLINE):
            exp = getattr(status, "expires", None)
            expires_in: Optional[float] = None
            try:
                if exp is not None:
                    expires_in = exp.timestamp() - time.time()
                    if expires_in <= 0:
                        await p.signal_offline(source + ":expired")
                        return False
            except Exception:
                expires_in = None
            await p.signal_online(source, expires_in=expires_in)
            return True
        if isinstance(status, _OFFLINE_TYPES):
            await p.signal_offline(source)
            return False
        return None

    async def probe_once(self) -> Optional[bool]:
        try:
            users = await self.app.client(functions.users.GetUsersRequest([types.InputUserSelf()]))
            status = getattr(users[0], "status", None) if users else None
        except Exception as e:
            err_log.warning(f"presence probe failed ({type(e).__name__})")
            return None
        return await self._apply_status(status, "poll")

    async def initial(self) -> None:
        online = await self.probe_once() if self.app.presence.enabled else None
        if self.app.presence.state.value == "unknown":
            await self.app.presence.initialize(bool(online))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="tfe-presence-poll")

    async def _loop(self) -> None:
        interval = max(self.min_interval, self.app.s.owner_status_check_interval)
        while True:
            try:
                await asyncio.sleep(interval)
                p = self.app.presence
                if p.enabled and (self.app.s.owner_status_poll or p.status_says_online()):
                    await self.probe_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                err_log.warning(f"presence loop error ({type(e).__name__})")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


def _media_info(msg: Any):
    if getattr(msg, "media", None) is None:
        return "", ""
    if getattr(msg, "photo", None):
        return "photo", ""
    if getattr(msg, "sticker", None):
        return "sticker", getattr(getattr(msg, "file", None), "emoji", "") or ""
    if getattr(msg, "voice", None):
        return "voice", ""
    if getattr(msg, "gif", None):
        return "gif", ""
    if getattr(msg, "video_note", None) or getattr(msg, "video", None):
        return "video", ""
    if getattr(msg, "audio", None):
        return "audio", ""
    mime = getattr(getattr(msg, "file", None), "mime_type", "") or ""
    if mime in ("image/jpeg", "image/png", "image/webp"):
        return "image_doc", ""
    if mime.startswith("video/"):
        return "video", ""
    return "other", ""


def register(app: Any) -> None:
    client, s = app.client, app.s
    owner = s.owner_id
    known_bots: Set[int] = set()

    @client.on(events.NewMessage(incoming=True))
    async def _on_incoming(event):
        try:
            msg = event.message
            if event.out or msg is None or getattr(msg, "action", None) is not None:
                return
            if event.is_channel and not event.is_group:
                return
            sender = event.sender
            if sender is None and event.is_private:
                try:
                    sender = await asyncio.wait_for(event.get_sender(), timeout=3.0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    sender = None
            uid = event.sender_id or 0
            is_bot = uid in known_bots or looks_like_bot(sender, msg, uid)
            if is_bot and uid:
                known_bots.add(uid)
            kind, emoji = _media_info(msg)
            voice_loader = None
            if kind == "voice" and s.voice_enabled:
                async def voice_loader(m=msg):
                    return await app.transport.download_audio(m)
            quote, quote_mine = await quote_of(event)
            loader = thumb_loader = None
            if kind in ("photo", "image_doc") and s.vision_enabled:
                async def loader(m=msg):
                    return await app.transport.download_image(m)
            elif kind in ("sticker", "gif") and s.vision_enabled:
                static = kind == "sticker" and (getattr(getattr(msg, "file", None), "mime_type", "") or "") in (
                    "image/webp", "image/png", "image/jpeg")

                async def loader(m=msg, th=not static):
                    return await app.transport.download_image(m, thumb=th)
            elif kind == "video" and s.vision_enabled and s.image_moderation:
                async def thumb_loader(m=msg):
                    return await app.transport.download_image(m, thumb=True)
            frames_loader = None
            if kind in ("video", "gif") and s.vision_enabled and s.image_moderation and s.video_moderation:
                async def frames_loader(n, m=msg):
                    return await app.transport.download_video_frames(m, n)
            inc = Incoming(
                chat_id=event.chat_id, message_id=msg.id, user_id=event.sender_id or 0,
                text=(event.raw_text or ""), has_media=msg.media is not None,
                is_private=bool(event.is_private), is_bot=is_bot,
                is_mentioned=bool(getattr(msg, "mentioned", False)),
                is_reply_to_me=False, date=msg.date.timestamp() if getattr(msg, "date", None) else 0.0,
                media_kind=kind, sticker_emoji=emoji, image_loader=loader, thumb_loader=thumb_loader,
                voice_loader=voice_loader, quote=quote, quote_mine=quote_mine, frames_loader=frames_loader,
                is_close_friend=bool(getattr(sender, "close_friend", False)),
                sender_name=" ".join(x for x in (getattr(sender, "first_name", "") or "",
                                                 getattr(sender, "last_name", "") or "") if x),
                sender_username=getattr(sender, "username", "") or "",
                is_contact=bool(getattr(sender, "contact", False)),
            )
            await app.transport.remember(event)
            await app.pipeline.handle(inc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.error(f"incoming handler error: {type(e).__name__}: {e}")

    @client.on(events.MessageEdited(incoming=True))
    async def _on_edited(event):
        try:
            msg = event.message
            if event.out or msg is None or (event.is_channel and not event.is_group):
                return
            if not s.moderate_edits or not (event.raw_text or "").strip():
                return
            sender = event.sender
            uid = event.sender_id or 0
            if not uid or uid == owner or uid in known_bots or looks_like_bot(sender, msg, uid):
                return
            quote, quote_mine = await quote_of(event)
            inc = Incoming(
                chat_id=event.chat_id, message_id=msg.id, user_id=uid, text=(event.raw_text or ""),
                has_media=msg.media is not None, is_private=bool(event.is_private), is_bot=False,
                is_mentioned=bool(getattr(msg, "mentioned", False)), quote=quote, quote_mine=quote_mine, edited=True,
                sender_name=" ".join(x for x in (getattr(sender, "first_name", "") or "",
                                                 getattr(sender, "last_name", "") or "") if x),
                sender_username=getattr(sender, "username", "") or "")
            await app.transport.remember(event)
            await app.pipeline.handle_edit(inc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.error(f"edit handler error: {type(e).__name__}: {e}")

    @client.on(events.NewMessage(from_users=owner))
    async def _on_owner(event):
        try:
            text = event.raw_text or ""
            if text.startswith(".") and app.commands.is_command(text):
                replies = await app.commands.run(text, event.sender_id, chat_id=event.chat_id)
                for r in replies or []:
                    await app.transport.send(event.chat_id, r[:3900], None)
                await app.presence.note_owner_message(None)
                return
            if not event.out:
                return
            if text.strip() and not text.startswith("."):
                replies = await app.commands.feed(text, event.sender_id, event.chat_id)
                if replies:
                    for r in replies:
                        await app.transport.send(event.chat_id, r[:3900], None)
                    await app.presence.note_owner_message(None)
                    return
            if app.transport.is_own_recent_send(event.chat_id):
                return
            if app.transport.was_sent_by_us(event.chat_id, getattr(event, "id", None)):
                return
            chat = event.chat_id if event.is_private and event.chat_id != owner else None
            await app.presence.note_owner_message(chat)
            if chat is not None:
                app.pipeline.note_owner_engaged(chat)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.error(f"owner handler error: {type(e).__name__}: {e}")

    @client.on(events.UserUpdate)
    async def _on_user_update(event):
        try:
            if getattr(event, "user_id", None) != owner:
                return
            if getattr(event, "typing", False):
                await app.presence.note_owner_message(None)
                return
            status = getattr(event, "status", None)
            if status is not None:
                await app.feed._apply_status(status, "event")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.error(f"user-update handler error: {type(e).__name__}: {e}")

    if _ACTIVITY_TYPES and s.owner_activity_signals:
        @client.on(events.Raw(_ACTIVITY_TYPES))
        async def _on_activity(update):
            try:
                await note_activity_update(app, update)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                err_log.error(f"activity handler error: {type(e).__name__}: {e}")

    tg_log.info("handlers registered")

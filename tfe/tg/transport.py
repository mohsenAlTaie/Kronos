from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Set, Tuple

from telethon import errors, functions
from telethon.tl.functions.users import GetFullUserRequest

from ..config import Settings
from ..logs import ERROR as err_log, TELEGRAM as log
from ..utils import split_message
from .stickers import StickerLibrary

_PERMANENT = tuple(
    getattr(errors, n) for n in (
        "ChatWriteForbiddenError", "UserIsBlockedError", "UserPrivacyRestrictedError", "InputUserDeactivatedError",
        "PeerFloodError", "ChatAdminRequiredError", "UserBannedInChannelError",
    ) if hasattr(errors, n)
)


def _brief(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:200]


def _sniff_image(data: bytes) -> Optional[str]:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"GIF8",):
        return "image/gif"
    return None


def extract_frames(path: str, n: int, max_bytes: int = 4_000_000):
    try:
        import av
        from io import BytesIO
    except Exception:
        return _extract_frames_cli(path, n, max_bytes)
    out: list = []
    try:
        with av.open(path) as container:
            stream = next((x for x in container.streams if x.type == "video"), None)
            if stream is None:
                return []
            total = float(container.duration or 0) / 1_000_000.0 if container.duration else 0.0
            if total <= 0 and stream.duration and stream.time_base:
                total = float(stream.duration * stream.time_base)
            marks = [total * (i + 0.5) / n for i in range(n)] if total > 0 else [0.0]
            for t in marks:
                try:
                    if t > 0:
                        container.seek(int(t / float(stream.time_base)), stream=stream, any_frame=False, backward=True)
                    for frame in container.decode(stream):
                        img = frame.to_image()
                        img.thumbnail((896, 896))
                        buf = BytesIO()
                        img.convert("RGB").save(buf, "JPEG", quality=82)
                        data = buf.getvalue()
                        if len(data) <= max_bytes:
                            out.append((data, "image/jpeg"))
                        break
                except Exception:
                    continue
    except Exception:
        return out
    return out


def _extract_frames_cli(path: str, n: int, max_bytes: int = 4_000_000):
    import shutil
    import subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        return []
    total = 0.0
    probe = shutil.which("ffprobe")
    if probe:
        try:
            r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                               capture_output=True, text=True, timeout=15)
            total = float((r.stdout or "0").strip() or 0)
        except Exception:
            total = 0.0
    marks = [total * (i + 0.5) / n for i in range(n)] if total > 0 else [0.0]
    out: list = []
    for t in marks:
        try:
            r = subprocess.run([ff, "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                                "-vf", "scale='min(896,iw)':-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
                               capture_output=True, timeout=25)
            if r.returncode == 0 and r.stdout and len(r.stdout) <= max_bytes:
                out.append((r.stdout, "image/jpeg"))
        except Exception:
            continue
    return out


class TelethonTransport:
    def __init__(self, client: Any, settings: Settings):
        self.client = client
        self.s = settings
        self._own_send_until: Dict[int, float] = {}
        self._flood_until = 0.0
        self._peers: Dict[int, Any] = {}
        self._sent: Deque[Tuple[int, int]] = deque(maxlen=3000)
        self._sent_set: Set[Tuple[int, int]] = set()
        self.stickers = StickerLibrary(client, settings)

    async def remember(self, event: Any) -> None:
        cid = getattr(event, "chat_id", None)
        if cid is None or cid in self._peers:
            return
        peer = None
        try:
            peer = getattr(event, "input_chat", None)
            if peer is None:
                getter = getattr(event, "get_input_chat", None)
                if getter is not None:
                    peer = await getter()
            if peer is None and getattr(event, "is_private", False):
                getter = getattr(event, "get_input_sender", None)
                if getter is not None:
                    peer = await getter()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug(f"remember peer failed chat={cid}: {type(e).__name__}")
            return
        if peer is not None:
            if len(self._peers) > 2000:
                self._peers.clear()
            self._peers[cid] = peer

    def _target(self, chat_id: int) -> Any:
        return self._peers.get(chat_id, chat_id)

    async def _refresh_entities(self, chat_id: int) -> None:
        try:
            await self.client.get_dialogs(limit=100)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug(f"get_dialogs failed: {type(e).__name__}")
        try:
            self._peers[chat_id] = await self.client.get_input_entity(chat_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def is_own_recent_send(self, chat_id: int) -> bool:
        return self._own_send_until.get(chat_id, 0.0) > time.monotonic()

    def _remember_sent(self, chat_id: int, sent: Any) -> None:
        mid = getattr(sent, "id", None)
        if not isinstance(mid, int):
            return
        if len(self._sent) == self._sent.maxlen:
            self._sent_set.discard(self._sent[0])
        self._sent.append((chat_id, mid))
        self._sent_set.add((chat_id, mid))

    def was_sent_by_us(self, chat_id: int, message_id: Any) -> bool:
        return (chat_id, message_id) in self._sent_set

    def own_send_recent(self) -> bool:
        now = time.monotonic()
        return any(v > now for v in self._own_send_until.values())

    def _mark_sending(self, chat_id: int, hold: float = 6.0) -> None:
        if len(self._own_send_until) > 1000:
            now = time.monotonic()
            self._own_send_until = {k: v for k, v in self._own_send_until.items() if v > now}
        self._own_send_until[chat_id] = time.monotonic() + hold

    async def send(self, chat_id: int, text: str, reply_to: Optional[int] = None) -> bool:
        if not text:
            return False
        if time.monotonic() < self._flood_until:
            log.warning(f"send skipped: flood-wait active for another {self._flood_until - time.monotonic():.0f}s")
            return False
        chunks = split_message(text, 3800)
        for i, chunk in enumerate(chunks):
            if not await self._send_one(chat_id, chunk, reply_to if i == 0 else None):
                return False
        return True

    async def _send_one(self, chat_id: int, text: str, reply_to: Optional[int]) -> bool:
        attempts = 0
        refreshed = False
        while attempts < 3:
            attempts += 1
            self._mark_sending(chat_id)
            try:
                sent = await self.client.send_message(self._target(chat_id), text, parse_mode=None, reply_to=reply_to)
                self._remember_sent(chat_id, sent)
                self._mark_sending(chat_id, 3.0)
                return True
            except errors.FloodWaitError as e:
                secs = int(getattr(e, "seconds", 0) or 0)
                if secs > self.s.max_flood_wait or attempts >= 2:
                    self._flood_until = time.monotonic() + min(secs, 600)
                    err_log.warning(f"FloodWait {secs}s — giving up on this send (cooldown set)")
                    return False
                log.warning(f"FloodWait {secs}s — waiting once")
                await asyncio.sleep(secs + 1)
            except errors.MessageTooLongError:
                parts = split_message(text, 1500)
                if len(parts) <= 1:
                    return False
                ok = True
                for p in parts:
                    ok = ok and await self._send_one(chat_id, p, None)
                return ok
            except errors.MessageIdInvalidError:
                reply_to = None
            except _PERMANENT as e:
                log.warning(f"cannot message chat={chat_id}: {type(e).__name__}")
                return False
            except (asyncio.TimeoutError, TimeoutError, ConnectionError):
                if attempts >= 2:
                    err_log.warning("send timed out twice — giving up")
                    return False
                await asyncio.sleep(1.0)
            except errors.RPCError as e:
                err_log.warning(f"send RPC error: {type(e).__name__}")
                if attempts >= 2:
                    return False
                await asyncio.sleep(1.0)
            except ValueError as e:
                if not refreshed:
                    refreshed = True
                    err_log.warning(f"send: peer not resolved for chat={chat_id} ({_brief(e)}) — refreshing dialogs")
                    await self._refresh_entities(chat_id)
                    continue
                err_log.error(f"send failed: ValueError: {_brief(e)}")
                return False
            except Exception as e:
                err_log.error(f"send failed: {type(e).__name__}: {_brief(e)}")
                return False
        return False

    async def typing(self, chat_id: int, seconds: float) -> None:
        try:
            async with self.client.action(self._target(chat_id), "typing"):
                await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(min(seconds, 0.4))

    async def block(self, user_id: int) -> bool:
        for attempt in (1, 2):
            try:
                await self.client(functions.contacts.BlockRequest(id=self._target(user_id)))
                log.info(f"user={user_id} blocked on Telegram")
                return True
            except errors.FloodWaitError as e:
                secs = int(getattr(e, "seconds", 0) or 0)
                if attempt == 2 or secs > self.s.max_flood_wait:
                    err_log.warning(f"BlockRequest FloodWait {secs}s — DB block stays in force")
                    return False
                await asyncio.sleep(secs + 1)
            except Exception as e:
                err_log.warning(f"BlockRequest failed ({type(e).__name__}) — DB block stays in force")
                return False
        return False

    async def unblock(self, user_id: int) -> bool:
        tried: list = []
        last: Any = None

        async def attempt(peer: Any) -> bool:
            nonlocal last
            if peer is None:
                return False
            try:
                await self.client(functions.contacts.UnblockRequest(id=peer))
                return True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last = e
                return False

        if await attempt(self._target(user_id)):
            return True
        tried.append("cache")
        peer = await self._find_in_blocked(user_id)
        if peer is not None:
            if await attempt(peer):
                self._peers[user_id] = peer
                log.info(f"user={user_id} unblocked on Telegram (via blocked list)")
                return True
            tried.append("blocked-list")
        elif await self._is_blocked_known(user_id) is False:
            return True
        await self._refresh_entities(user_id)
        if await attempt(self._peers.get(user_id)):
            log.info(f"user={user_id} unblocked on Telegram (via dialogs)")
            return True
        try:
            ent = await self.client.get_input_entity(user_id)
            if await attempt(ent):
                self._peers[user_id] = ent
                return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last = e
        err_log.warning(f"UnblockRequest failed for {user_id} ({type(last).__name__ if last else '?'}: {last})")
        return False

    async def _find_in_blocked(self, user_id: int) -> Any:
        offset = 0
        try:
            for _ in range(20):
                res = await self.client(functions.contacts.GetBlockedRequest(offset=offset, limit=100))
                users = list(getattr(res, "users", None) or [])
                for u in users:
                    if getattr(u, "id", None) == user_id:
                        try:
                            return await self.client.get_input_entity(u)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            from telethon import types as _t
                            return _t.InputPeerUser(user_id=u.id, access_hash=getattr(u, "access_hash", 0) or 0)
                got = len(getattr(res, "blocked", None) or users)
                if got < 100:
                    break
                offset += got
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug(f"GetBlocked failed: {type(e).__name__}")
        return None

    async def _is_blocked_known(self, user_id: int) -> Optional[bool]:
        try:
            res = await self.client(functions.contacts.GetBlockedRequest(offset=0, limit=100))
            total = int(getattr(res, "count", 0) or 0) or len(getattr(res, "users", None) or [])
            if total > 100:
                return None
            present = any(getattr(u, "id", None) == user_id for u in (res.users or []))
            return None if present else False
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def sender_info(self, user_id: int) -> Dict[str, Any]:
        target = self._target(user_id)
        try:
            full = await asyncio.wait_for(self.client(GetFullUserRequest(target)), timeout=4.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug(f"sender_info failed user={user_id}: {type(e).__name__}")
            return {}
        info: Dict[str, Any] = {"ok": True, "bio": ""}
        if self.s.include_sender_bio:
            fu = getattr(full, "full_user", None)
            info["bio"] = getattr(fu, "about", "") or ""
            b = getattr(fu, "birthday", None)
            if b is not None and getattr(b, "day", None) and getattr(b, "month", None):
                info["birthday"] = {"day": int(b.day), "month": int(b.month),
                                    "year": int(b.year) if getattr(b, "year", None) else None}
        users = getattr(full, "users", None) or []
        ent = next((u for u in users if getattr(u, "id", None) == user_id), users[0] if users else None)
        if ent is not None:
            info["name"] = " ".join(x for x in (getattr(ent, "first_name", "") or "", getattr(ent, "last_name", "") or "") if x)
            info["username"] = getattr(ent, "username", "") or ""
            info["contact"] = bool(getattr(ent, "contact", False))
        return info

    async def download_image(self, message: Any, thumb: bool = False):
        limit = self.s.max_image_bytes
        try:
            size = getattr(getattr(message, "file", None), "size", None)
            if size and size > limit and not thumb:
                log.info(f"image skipped: {size} bytes > limit {limit}")
                return None
            if thumb:
                data = await asyncio.wait_for(self.client.download_media(message, file=bytes, thumb=-1), timeout=20.0)
            else:
                data = await asyncio.wait_for(self.client.download_media(message, file=bytes), timeout=20.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.warning(f"image download failed ({type(e).__name__}: {_brief(e)})")
            return None
        if not data or not isinstance(data, (bytes, bytearray)) or len(data) > limit:
            return None
        mime = _sniff_image(bytes(data[:16]))
        return (bytes(data), mime) if mime else None

    async def download_video_frames(self, message: Any, n: int = 3):
        n = max(1, min(int(n or 3), 6))
        f = getattr(message, "file", None)
        dur = int(getattr(f, "duration", 0) or 0)
        size = getattr(f, "size", None)
        frames: list = []
        if not ((size and size > self.s.video_max_bytes) or (dur and dur > self.s.video_max_seconds)):
            import os
            import tempfile
            path = ""
            try:
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    path = tmp.name
                got = await asyncio.wait_for(self.client.download_media(message, file=path), timeout=60.0)
                if got:
                    frames = await asyncio.to_thread(extract_frames, path, n, self.s.max_image_bytes)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                err_log.warning(f"video download failed ({type(e).__name__}: {_brief(e)})")
            finally:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
        if not frames:
            thumb = await self.download_image(message, thumb=True)
            if thumb:
                frames = [thumb]
        return frames

    async def send_sticker(self, chat_id: int, emoji: str) -> bool:
        doc = await self.stickers.pick(emoji, chat_id)
        if doc is None:
            return False
        self._mark_sending(chat_id)
        try:
            sent = await self.client.send_file(self._target(chat_id), doc)
            self._remember_sent(chat_id, sent)
            self._mark_sending(chat_id, 3.0)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.warning(f"sticker send failed ({type(e).__name__}: {_brief(e)})")
            return False

    def sticker_emojis(self, n: int = 30) -> list:
        return self.stickers.top_emojis(n)

    async def download_audio(self, message: Any):
        f = getattr(message, "file", None)
        dur = int(getattr(f, "duration", 0) or 0)
        size = getattr(f, "size", None)
        if dur > self.s.voice_max_seconds or (size and size > self.s.voice_max_bytes):
            log.info(f"voice skipped: {dur}s / {size} bytes (limits {self.s.voice_max_seconds}s / {self.s.voice_max_bytes})")
            return None
        try:
            data = await asyncio.wait_for(self.client.download_media(message, file=bytes), timeout=25.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.warning(f"voice download failed ({type(e).__name__}: {_brief(e)})")
            return None
        if not data or len(data) > self.s.voice_max_bytes:
            return None
        return bytes(data), (getattr(f, "mime_type", "") or "audio/ogg"), dur

    async def resolve_user(self, ref: str) -> Optional[Tuple[int, str]]:
        ref = (ref or "").strip()
        m = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,})/?$", ref)
        target: Any = m.group(1) if m else (ref[1:] if ref.startswith("@") else ref)
        if isinstance(target, str) and target.isdigit():
            target = int(target)
        if isinstance(target, str) and not re.match(r"^[A-Za-z0-9_]{4,}$", target):
            return None
        try:
            ent = await asyncio.wait_for(self.client.get_entity(self._target(target) if isinstance(target, int) else target),
                                         timeout=6.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            return (target, "") if isinstance(target, int) else None
        if getattr(ent, "bot", False) or not hasattr(ent, "first_name"):
            return None
        name = " ".join(x for x in (getattr(ent, "first_name", "") or "", getattr(ent, "last_name", "") or "") if x)
        return int(ent.id), name

    async def delete_chat(self, chat_id: int, revoke: bool = True) -> bool:
        target = self._target(chat_id)
        if isinstance(target, int):
            try:
                target = await self.client.get_input_entity(chat_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                return False
        if type(target).__name__ != "InputPeerUser":
            err_log.warning("delete_chat refused: target is not a private user chat")
            return False
        try:
            for _ in range(12):
                res = await self.client(functions.messages.DeleteHistoryRequest(
                    peer=target, max_id=0, revoke=bool(revoke)))
                if not getattr(res, "offset", 0):
                    break
            log.info(f"chat {chat_id} deleted (revoke={bool(revoke)})")
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.warning(f"delete chat failed ({type(e).__name__}: {_brief(e)})")
            return False

    async def notify_owner(self, text: str) -> None:
        try:
            await self.client.send_message(self.s.owner_id, text[:3900], parse_mode=None)
        except Exception as e:
            err_log.warning(f"owner notification failed ({type(e).__name__})")

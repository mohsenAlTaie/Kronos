from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any, Optional

from .ai.prompts import load_persona
from .ai.roles import Role
from .ai.router import AIModelRouter
from .config import Settings
from .logs import ERROR as err_log, TELEGRAM as log, setup_logging
from .memory.db import Database, adopt_legacy_db
from .memory.manager import MemoryManager
from .moderation.classifier import Moderator, SpamDetector
from .moderation.service import ModerationService
from .pipeline import MessagePipeline
from .presence import PresenceManager
from .search import WebSearch


class App:
    def __init__(self, settings: Settings):
        self.s = settings
        self.client: Any = None
        self.transport: Any = None
        self.profile: Any = None
        self.feed: Any = None
        self.control: Any = None
        self.commands: Any = None
        self.scheduler: Any = None
        self._stopping = False

        self.db = Database(settings.db_path)
        self.router = AIModelRouter(settings)
        self.search = WebSearch(settings)
        self.persona = load_persona(settings.persona_file)
        self.presence = PresenceManager(enabled=settings.smart_presence_enabled, **settings.presence_params())
        self.presence.ai_manual_enabled = settings.ai_reply_enabled
        self.moderator = Moderator(router=self.router, threshold=settings.moderation_confidence,
                                   llm_mode=settings.moderation_llm_mode, timeout=settings.moderation_timeout,
                                   spam=SpamDetector(), search=self.search,
                                   web_lookup=settings.moderation_web_lookup, confirm=settings.moderation_confirm)
        self.moderation = ModerationService(settings, self.db, self.moderator)
        self.memory = MemoryManager(self.db, settings, self.router)
        self.pipeline: Optional[MessagePipeline] = None

    async def cleanup(self) -> dict:
        try:
            archived = await self.memory.archive_expired()
            if archived:
                log.info(f"archived {archived} old conversations into summaries")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.warning(f"archive step skipped: {type(e).__name__}")
        res = await self.db.cleanup(self.s.memory_ttl_days, self.s.memory_hard_ttl_days)
        log.info(f"cleanup done: {res}")
        return res

    async def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        from .tg import handlers
        from .tg.commands import OwnerCommands
        from .tg.profile import ProfileUpdater
        from .tg.transport import TelethonTransport

        s = self.s
        adopt_legacy_db(s.db_path)
        await self.db.open()
        await self.moderation.load()
        await self._seed_partner()

        self.client = TelegramClient(StringSession(s.session_string), s.api_id, s.api_hash,
                                     device_model=s.device_model, flood_sleep_threshold=0)
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("SESSION_STRING is not authorised — regenerate it with generate_session.py")

        self.transport = TelethonTransport(self.client, s)
        self.profile = ProfileUpdater(self.client, s)
        self.pipeline = MessagePipeline(settings=s, transport=self.transport, presence=self.presence,
                                        moderation=self.moderation, memory=self.memory, router=self.router,
                                        search=self.search, persona=self.persona, owner_id=s.owner_id)
        self.feed = handlers.PresenceFeed(self)
        self.commands = OwnerCommands(self)
        handlers.register(self)

        await self.feed.initial()
        self.feed.start()

        self.scheduler = AsyncIOScheduler(timezone=s.tz)
        opts = dict(max_instances=1, coalesce=True, misfire_grace_time=30)
        self.scheduler.add_job(self.profile.update_name_time, "interval", seconds=s.update_interval, id="name", **opts)
        self.scheduler.add_job(self.profile.update_bio, "interval", minutes=s.bio_interval_minutes, id="bio", **opts)
        self.scheduler.add_job(self.cleanup, "interval", hours=s.cleanup_interval_hours, id="cleanup", **opts)
        self.scheduler.start()

        await self.profile.update_name_time()
        await self.profile.update_bio()
        await self.cleanup()
        from .tg.control_bot import start_control_bot
        self.control = await start_control_bot(self)
        if self.control is None and self.pipeline is not None:
            self.pipeline.on_moderation = self._alert_owner_text
        self._banner()
        self._warn_persistence()
        await self._warn_models()
        self._preflight_task = asyncio.create_task(self._preflight())
        if s.stickers_enabled and s.sticker_source != "search":
            self.transport.stickers.warm()

    def _banner(self) -> None:
        s = self.s
        log.info("=" * 46)
        log.info(f"running as {self.profile.me_first_name} (@{self.profile.me_username})")
        try:
            from importlib.metadata import version
            log.info("libs " + " ".join(f"{n}={version(n)}" for n in ("telethon", "litellm", "APScheduler")))
        except Exception:
            pass
        log.info(f"models chat={self.router.primary_for(Role.CHAT)} fast={self.router.primary_for(Role.FAST)} "
                 f"reasoning={self.router.primary_for(Role.REASONING)} moderation={self.router.primary_for(Role.MODERATION)}")
        log.info(f"AI_REPLY_ENABLED={self.presence.ai_manual_enabled} MODERATION_ENABLED={self.moderation.enabled} "
                 f"DRAFT_MODE={self.pipeline.draft_mode} whitelist={'all' if not s.whitelist else len(s.whitelist)}")
        log.info(f"presence={self.presence.state.value} smart={self.presence.enabled} "
                 f"wake_after={self.presence.grace_period:.0f}s chat_cooldown={self.presence.chat_cooldown:.0f}s "
                 f"activity_signals={s.owner_activity_signals} status_poll={s.owner_status_poll} "
                 f"policy=warning→block(max_warnings={s.max_warnings}, ttl={s.warning_ttl_hours}h)")
        log.info(f"hold_replies={s.hold_replies}({s.hold_minutes:g}min) stickers={s.stickers_enabled} "
                 f"control_bot={'on' if self.control else 'off'} voice={s.voice_enabled} trust_contacts={s.trust_contacts}")
        if not self.presence.enabled:
            log.warning("SMART_PRESENCE_ENABLED is OFF → the AI answers even while you are inside the account. "
                        "Set SMART_PRESENCE_ENABLED=true so it sleeps when you are present.")
        log.info("=" * 46)

    async def _seed_partner(self) -> None:
        from .ai.relationship import partner_of
        p = partner_of(self.persona)
        if not p:
            return
        uid, name = int(p["user_id"]), str(p.get("name") or "آية")
        self.moderation.set_always_trusted(uid, "love", name)
        key = f"partner_seeded:{uid}"
        if not await self.db.get_meta(key):
            if uid not in {r["user_id"] for r in await self.moderation.trust_list()}:
                await self.moderation.trust_add(uid, "love", name)
            await self.db.set_meta(key, "1")

    async def _alert_owner_text(self, info: dict) -> None:
        from .control import ControlPanel
        text, _ = await ControlPanel(self).alert(info)
        cmd = f"\n.unblock {info['user_id']}" if info.get("action") == "block" else f"\n.unwarn {info['user_id']}"
        await self.transport.send(self.s.owner_id, text + cmd, None)

    def _warn_persistence(self) -> None:
        import os
        d = os.path.dirname(os.path.abspath(self.s.db_path))
        try:
            mounted = os.path.ismount(d) or os.path.ismount(os.path.dirname(d))
        except OSError:
            mounted = True
        if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"):
            vol = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "")
            if not (vol or mounted):
                log.warning(f"DATABASE IS NOT ON A VOLUME ({d}) — bans, warnings and memory are erased on every deploy. "
                            "Add a Railway Volume mounted at /app/data (or set DATA_DIR to the volume path).")

    async def _preflight(self) -> None:
        try:
            await self.router.preflight()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_log.warning(f"preflight skipped: {type(e).__name__}")

    async def _warn_models(self) -> None:
        try:
            for row in await asyncio.to_thread(self.router.availability):
                if not row["ok"]:
                    err_log.warning(f"model check: {row['model']} → {row['note']}")
        except Exception:
            pass

    async def run_forever(self) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        disc = asyncio.create_task(self.client.run_until_disconnected())
        waiter = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({disc, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if disc in done and not stop.is_set():
            err_log.error("Telegram connection ended unexpectedly")
        for t in (disc, waiter):
            t.cancel()
        await asyncio.gather(disc, waiter, return_exceptions=True)

    async def _stop_scheduler(self) -> None:
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass

    async def _noop(self) -> None:
        return None

    async def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        log.info("shutting down…")
        steps = [
            ("scheduler", self._stop_scheduler),
            ("presence feed", self.feed.stop if self.feed else self._noop),
            ("pipeline", self.pipeline.shutdown if self.pipeline else self._noop),
            ("presence", self.presence.stop),
            ("stickers", self.transport.stickers.close if self.transport is not None else self._noop),
            ("control bot", self.control.disconnect if self.control is not None else self._noop),
            ("telegram", self.client.disconnect if self.client is not None else self._noop),
            ("database", self.db.close),
        ]
        for name, fn in steps:
            try:
                await asyncio.wait_for(fn(), timeout=8)
            except Exception as e:
                err_log.warning(f"shutdown step '{name}' failed: {type(e).__name__}")


async def amain() -> int:
    settings = Settings.from_env()
    setup_logging(settings.log_level, settings.secrets())
    fatal = False
    for level, msg in settings.validate():
        if level == "fatal":
            err_log.error(msg)
            fatal = True
        else:
            log.warning(msg)
    if fatal:
        return 2
    app = App(settings)
    try:
        await app.start()
        await app.run_forever()
        return 0
    except Exception as e:
        err_log.error(f"fatal: {type(e).__name__}: {e}")
        return 1
    finally:
        await app.stop()


def run() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    try:
        code = asyncio.run(amain())
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)

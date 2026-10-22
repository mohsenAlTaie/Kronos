from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional, Set, Tuple

from ..logs import PRESENCE as log


class PresenceState(str, Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    GRACE = "grace"
    OFFLINE = "offline"


@dataclass(frozen=True)
class Ticket:
    manager: "PresenceManager"
    epoch: int
    chat_id: Optional[int]

    def valid(self) -> bool:
        return self.manager.epoch == self.epoch and self.manager.ai_allowed(self.chat_id)


class PresenceManager:
    def __init__(
        self,
        *,
        enabled: bool = True,
        grace_period: float = 6.0,
        idle_hold: float = 15.0,
        chat_cooldown: float = 90.0,
        status_ttl: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ):
        self.enabled = enabled
        self.grace_period = grace_period
        self.idle_hold = idle_hold
        self.chat_cooldown = chat_cooldown
        self.status_ttl = status_ttl
        self._clock = clock
        self._wall = wall

        self.transition_lock = asyncio.Lock()
        self.ai_manual_enabled = True
        self.owner_last_seen: float = 0.0
        self.last_state_change: float = wall()
        self.epoch = 0

        self._state = PresenceState.UNKNOWN if enabled else PresenceState.OFFLINE
        self._status_online = False
        self._status_expires: Optional[float] = None
        self._activity_until = 0.0
        self._offline_until = 0.0
        self._grace_deadline = 0.0
        self._recent: Deque[Tuple[float, str]] = deque(maxlen=12)
        self.on_wake: Optional[Callable[[], None]] = None
        self._timer: Optional[asyncio.Task] = None
        self._tracked: Set[asyncio.Task] = set()
        self._chat_cooldown_until: Dict[int, float] = {}
        self.cancelled_generations = 0

    @property
    def state(self) -> PresenceState:
        return self._state

    @property
    def owner_online(self) -> bool:
        return self._state == PresenceState.ONLINE

    @property
    def ai_enabled(self) -> bool:
        return self.ai_allowed(None)

    def ai_allowed(self, chat_id: Optional[int] = None) -> bool:
        if not self.ai_manual_enabled:
            return False
        if not self.enabled:
            return True
        if self._state != PresenceState.OFFLINE:
            return False
        if chat_id is not None:
            until = self._chat_cooldown_until.get(chat_id)
            if until is not None:
                if self._clock() < until:
                    return False
                self._chat_cooldown_until.pop(chat_id, None)
        return True

    def status_says_online(self) -> bool:
        return self._status_online

    def explain(self) -> str:
        sn = self.snapshot()
        bits = [f"state={sn['state']}"]
        if sn["status_online"]:
            exp = sn["status_expires_in"]
            bits.append("status=online" + (f"(expires in {exp:.0f}s)" if exp is not None else ""))
        if sn["wake_in"]:
            bits.append(f"wakes in {sn['wake_in']:.0f}s")
        if sn["recent_signals"]:
            bits.append("last: " + ", ".join(f"{src} {age:.0f}s ago" for age, src in sn["recent_signals"][:3]))
        return " ".join(bits)

    def block_reason(self, chat_id: Optional[int] = None) -> str:
        if not self.ai_manual_enabled:
            return "manual"
        if not self.enabled:
            return ""
        if self._state != PresenceState.OFFLINE:
            return "owner"
        if chat_id is not None:
            until = self._chat_cooldown_until.get(chat_id)
            if until is not None and self._clock() < until:
                return "chat"
        return ""

    def ticket(self, chat_id: Optional[int] = None) -> Ticket:
        return Ticket(self, self.epoch, chat_id)

    def snapshot(self) -> dict:
        now = self._clock()
        return {
            "enabled": self.enabled,
            "state": self._state.value,
            "owner_online": self.owner_online,
            "ai_manual_enabled": self.ai_manual_enabled,
            "ai_allowed": self.ai_allowed(None),
            "epoch": self.epoch,
            "grace_remaining": max(0.0, self._offline_until - now) if self._state == PresenceState.GRACE else 0.0,
            "activity_hold_remaining": max(0.0, self._activity_until - now),
            "wake_in": (max(0.0, max(self._activity_until, self._offline_until) - now)
                        if self._state != PresenceState.OFFLINE and not self._status_online else 0.0),
            "status_online": self._status_online,
            "status_expires_in": (max(0.0, self._status_expires - now)
                                  if self._status_online and self._status_expires is not None else None),
            "recent_signals": [(round(self._wall() - t, 1), src) for t, src in reversed(self._recent)],
            "seconds_since_last_seen": (self._wall() - self.owner_last_seen) if self.owner_last_seen else None,
            "seconds_since_change": self._wall() - self.last_state_change,
            "tracked_generations": len(self._tracked),
            "cancelled_generations": self.cancelled_generations,
        }

    def _set_online_ttl(self, expires_in: Optional[float]) -> None:
        ttl = self.status_ttl if expires_in is None else expires_in
        self._status_expires = self._clock() + max(0.01, min(float(ttl), 900.0))

    async def initialize(self, online: Optional[bool], expires_in: Optional[float] = None) -> None:
        async with self.transition_lock:
            if not self.enabled:
                return
            if online:
                self._status_online = True
                self._set_online_ttl(expires_in)
                self.owner_last_seen = self._wall()
                self._transition(PresenceState.ONLINE, "initial", bump=True)
            else:
                self._status_online = False
                self._transition(PresenceState.OFFLINE, "initial")
            self._arm_timer()

    def _note(self, what: str) -> None:
        self._recent.append((self._wall(), what))

    async def signal_online(self, source: str = "status", expires_in: Optional[float] = None) -> None:
        if not self.enabled:
            return
        async with self.transition_lock:
            if not self._status_online:
                self._note(f"status:online({source})")
            self._status_online = True
            self._set_online_ttl(expires_in)
            self.owner_last_seen = self._wall()
            await self._reconcile(source)

    async def signal_offline(self, source: str = "status") -> None:
        if not self.enabled:
            return
        async with self.transition_lock:
            if self._status_online:
                self._note(f"status:offline({source})")
                self._offline_until = (self._clock() + self.grace_period) if self.grace_period > 0 else 0.0
            self._status_online = False
            self._status_expires = None
            self.owner_last_seen = self._wall()
            await self._reconcile(source)

    async def force_offline(self, source: str = "manual") -> None:
        if not self.enabled:
            return
        async with self.transition_lock:
            self._note(f"forced offline({source})")
            self._status_online = False
            self._status_expires = None
            self._activity_until = 0.0
            self._offline_until = 0.0
            self._chat_cooldown_until.clear()
            await self._reconcile(source)

    async def note_owner_message(self, chat_id: Optional[int], source: str = "owner_message") -> None:
        if not self.enabled:
            return
        async with self.transition_lock:
            now = self._clock()
            self._activity_until = max(self._activity_until, now + self.idle_hold)
            self.owner_last_seen = self._wall()
            self._note(source)
            if chat_id is not None and self.chat_cooldown > 0:
                if len(self._chat_cooldown_until) > 500:
                    self._chat_cooldown_until = {
                        k: v for k, v in self._chat_cooldown_until.items() if v > now
                    }
                self._chat_cooldown_until[chat_id] = now + self.chat_cooldown
            await self._reconcile("owner_message")

    async def set_enabled(self, enabled: bool) -> None:
        async with self.transition_lock:
            self.enabled = enabled
            if not enabled:
                self._cancel_timer()
            else:
                self._transition(PresenceState.UNKNOWN, "reenabled")
                await self._reconcile("reenabled")

    def track(self, task: asyncio.Task) -> None:
        self._tracked.add(task)
        task.add_done_callback(self._tracked.discard)

    def _cancel_tracked(self) -> None:
        for t in list(self._tracked):
            if not t.done():
                t.cancel()
                self.cancelled_generations += 1
        self._tracked.clear()

    def _transition(self, new: PresenceState, source: str, bump: bool = False) -> None:
        old = self._state
        if old == new and not bump:
            return
        self._state = new
        self.last_state_change = self._wall()
        if bump:
            self.epoch += 1
        log.info(f"owner={new.value} ai={'enabled' if self.ai_allowed(None) else 'disabled'} "
                 f"({old.value}->{new.value} via {source}, epoch={self.epoch})")

    async def _reconcile(self, source: str) -> None:
        now = self._clock()
        if self._status_online and self._status_expires is not None and now >= self._status_expires:
            self._note(f"status:expired({source})")
            self._status_online = False
            self._offline_until = max(self._offline_until, self._status_expires + max(self.grace_period, 0.0))
            self._status_expires = None
        present_until = max(self._activity_until, self._offline_until)
        want_online = self._status_online or now < present_until
        if want_online:
            if self._state in (PresenceState.OFFLINE, PresenceState.UNKNOWN):
                self._transition(PresenceState.ONLINE, source, bump=True)
                self._cancel_tracked()
            elif self._state == PresenceState.GRACE and self._status_online:
                self._transition(PresenceState.ONLINE, source, bump=True)
                self._cancel_tracked()
            elif (self._state == PresenceState.ONLINE and not self._status_online
                  and self._offline_until > self._activity_until and now < self._offline_until):
                self._transition(PresenceState.GRACE, source)
        elif self._state != PresenceState.OFFLINE:
            was = self._state
            self._transition(PresenceState.OFFLINE, "grace_elapsed" if self._state == PresenceState.GRACE
                             else ("idle_elapsed" if source == "timer" else source))
            if was in (PresenceState.ONLINE, PresenceState.GRACE) and self.on_wake is not None:
                try:
                    self.on_wake()
                except Exception as e:
                    log.warning(f"on_wake callback failed: {type(e).__name__}")
        self._arm_timer()

    def _cancel_timer(self) -> None:
        t = self._timer
        if t is not None and t is not asyncio.current_task() and not t.done():
            t.cancel()
        self._timer = None

    def _arm_timer(self) -> None:
        self._cancel_timer()
        now = self._clock()
        deadline: Optional[float] = None
        if self._state in (PresenceState.ONLINE, PresenceState.GRACE) and not self._status_online:
            deadline = max(self._activity_until, self._offline_until)
        elif self._status_online and self._status_expires is not None:
            deadline = self._status_expires
        if deadline is None:
            return
        try:
            self._timer = asyncio.get_running_loop().create_task(self._sleep_then_reconcile(deadline - now))
        except RuntimeError:
            self._timer = None

    async def _sleep_then_reconcile(self, delay: float) -> None:
        try:
            await asyncio.sleep(max(0.0, delay) + 0.005)
        except asyncio.CancelledError:
            return
        async with self.transition_lock:
            await self._reconcile("timer")

    async def stop(self) -> None:
        self._cancel_timer()
        self._cancel_tracked()

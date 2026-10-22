from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..config import Settings
from ..logs import AI as log
from ..utils import strip_reasoning
from .roles import Role

CompletionFn = Callable[..., Awaitable[str]]

_CODER = re.compile(r"(coder|codestral|starcoder|code-?llama|codegemma|deepseek-coder|devstral)", re.I)


class AIUnavailable(Exception):

    def __init__(self, reason: str, attempts: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.attempts = attempts


@dataclass
class Completion:
    text: str
    model: str
    role: str
    latency: float
    attempts: int
    fallback_used: bool


@dataclass
class _Breaker:
    failures: int = 0
    open_until: float = 0.0
    last_error: str = ""
    total_ok: int = 0
    total_fail: int = 0
    probing: bool = False


def classify_error(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in name or "timed out" in msg:
        return "timeout"
    if "ratelimit" in name or "ratelimit" in msg or "rate limit" in msg or "429" in msg or "quota" in msg:
        return "rate_limit"
    if any(k in name for k in ("authentication", "permissiondenied", "badrequest", "notfound",
                               "invalidrequest", "unprocessable", "contentpolicy")):
        return "fatal"
    if any(k in msg for k in ("invalid api key", "incorrect api key", "unauthorized", "model not found",
                              "does not exist", "no such model", "401", "403", "404")):
        return "fatal"
    return "transient"


_RETRY = re.compile(r"(?:try again in|retry[- ]after[: ]*|retry in)\s*((?:\d+(?:\.\d+)?\s*(?:ms|h|m|s)\s*)+)", re.I)
_UNIT = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_retry_after(msg: str) -> Optional[float]:
    m = _RETRY.search(msg or "")
    if not m:
        return None
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)", m.group(1), re.I):
        total += float(num) * _UNIT[unit.lower()]
    return total or None


def _short_err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:80]}".replace("\n", " ")


class AIModelRouter:
    def __init__(self, settings: Settings, completion_fn: Optional[CompletionFn] = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.s = settings
        self._fn = completion_fn or self._litellm_completion
        self._clock = clock
        self._sleep = sleep
        self._breakers: Dict[str, _Breaker] = {}
        self._sem = asyncio.Semaphore(settings.max_concurrent_ai)
        self._overrides: Dict[str, str] = {}
        self.stats: Dict[str, int] = {"calls": 0, "ok": 0, "failed": 0, "fallbacks": 0, "retries": 0, "breaker_skips": 0}
        self.last_model_used: Dict[str, str] = {}

    def primary_for(self, role: Role) -> str:
        return self._overrides.get(role.value) or self.s.model_for(role.value)

    _SIBLINGS = {
        Role.FAST: (Role.CHAT,),
        Role.CHAT: (Role.FAST,),
        Role.REASONING: (Role.CHAT,),
        Role.MODERATION: (Role.FAST,),
        Role.SUMMARY: (Role.FAST, Role.CHAT),
        Role.SAFEGUARD: (Role.MODERATION, Role.CHAT),
    }

    def chain(self, role: Role) -> List[str]:
        first = self.primary_for(role)
        explicit = self.s.role_fallbacks.get(role.value)
        if role == Role.VISION:
            fb = list(explicit or self.s.vision_fallbacks)
        elif explicit:
            fb = list(explicit)
        else:
            fb = [self.primary_for(r) for r in self._SIBLINGS.get(role, ())] + list(self.s.global_fallbacks)
        out: List[str] = []
        if first:
            out.append(first)
        for m in fb:
            if not m:
                continue
            if m in out:
                continue
            if not self.s.allow_coder_models and _CODER.search(m):
                continue
            out.append(m)
        return out

    def set_model(self, role: Role, model: str) -> None:
        self._overrides[role.value] = model.strip()

    def _b(self, model: str) -> _Breaker:
        b = self._breakers.get(model)
        if b is None:
            b = self._breakers[model] = _Breaker()
        return b

    def _available(self, model: str) -> bool:
        b = self._b(model)
        now = self._clock()
        if b.open_until <= now:
            return True
        return False

    def _ok(self, model: str) -> None:
        b = self._b(model)
        b.failures, b.open_until, b.total_ok = 0, 0.0, b.total_ok + 1

    def wait_hint(self, role: Role) -> float:
        now = self._clock()
        best: Optional[float] = None
        for m in self.chain(role):
            rem = self._b(m).open_until - now
            if rem <= 0:
                return 0.0
            best = rem if best is None else min(best, rem)
        return best or 0.0

    def _fail(self, model: str, kind: str, err: str, retry_after: Optional[float] = None) -> None:
        b = self._b(model)
        b.failures += 1
        b.total_fail += 1
        b.last_error = err
        now = self._clock()
        if kind == "rate_limit":
            cool = (retry_after + 0.4) if retry_after else min(self.s.breaker_cooldown, 20.0)
            b.open_until = now + max(1.0, min(cool, 3600.0))
        elif kind == "fatal":
            dead = any(k in err.lower() for k in ("notfound", "does not exist", "model not found", "decommission",
                                                   "no such model", "404"))
            b.open_until = now + (3600.0 if dead else max(self.s.breaker_cooldown, 120.0))
            if dead:
                log.error(f"model {model} does not exist or is retired — change the matching *_MODEL variable "
                          f"(skipping it for 1h)")
        elif b.failures >= self.s.breaker_threshold:
            b.open_until = now + self.s.breaker_cooldown
        if b.open_until > now:
            log.warning(f"circuit open for {model} ({kind}) for {b.open_until - now:.0f}s")

    async def complete(self, role: Role, messages: List[Dict[str, str]], *, max_tokens: int = 350,
                       temperature: float = 0.7, timeout: Optional[float] = None,
                       deadline: Optional[float] = None, max_models: Optional[int] = None,
                       max_retries: Optional[int] = None, extra: Optional[Dict[str, Any]] = None) -> Completion:
        timeout = timeout if timeout is not None else self.s.ai_timeout
        deadline_s = deadline if deadline is not None else self.s.max_ai_latency
        retries = self.s.ai_max_retries if max_retries is None else max_retries
        start = self._clock()
        hard_stop = start + deadline_s
        chain = self.chain(role)
        if not chain:
            raise AIUnavailable("no_model_configured")
        if max_models:
            chain = chain[:max_models]
        self.stats["calls"] += 1
        attempts = 0
        tried_any = False
        last_err = "no_attempt"

        for idx, model in enumerate(chain):
            if not self._available(model):
                self.stats["breaker_skips"] += 1
                continue
            tried_any = True
            for attempt in range(retries + 1):
                remaining = hard_stop - self._clock()
                if remaining < 0.4:
                    self.stats["failed"] += 1
                    raise AIUnavailable(f"deadline ({last_err})", attempts)
                attempts += 1
                try:
                    base = ({**self.s.chat_extra_params, **(extra or {})} if role != Role.MODERATION
                            else dict(extra or {}))
                    extras, tokens = self._model_tuning(model, base, max_tokens)
                    text = await self._one(model, messages, tokens, temperature, min(timeout, remaining), extras)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    kind = classify_error(e)
                    last_err = f"{model}:{kind}"
                    self._fail(model, kind, _short_err(e), parse_retry_after(str(e)) if kind == "rate_limit" else None)
                    log.warning(f"{role.value}/{model} failed ({kind}) attempt {attempt + 1}: {_short_err(e)}")
                    if kind in ("fatal", "rate_limit") or not self._available(model):
                        break
                    if attempt < retries:
                        self.stats["retries"] += 1
                        back = min(0.4 * (2 ** attempt), 2.0) * (0.75 + random.random() / 2)
                        if self._clock() + back >= hard_stop - 0.4:
                            break
                        await self._sleep(back)
                        continue
                    break
                else:
                    self._ok(model)
                    self.stats["ok"] += 1
                    used_fb = idx > 0 or model != self.primary_for(role)
                    if used_fb:
                        self.stats["fallbacks"] += 1
                    self.last_model_used[role.value] = model
                    return Completion(text, model, role.value, self._clock() - start, attempts, used_fb)

        self.stats["failed"] += 1
        raise AIUnavailable(last_err if tried_any else "all_circuits_open", attempts)

    @staticmethod
    def _model_tuning(model: str, extra: Dict[str, Any], max_tokens: int) -> "tuple[Dict[str, Any], int]":
        lm = model.lower()
        if "gpt-oss" in lm:
            extra = dict(extra)
            extra.setdefault("reasoning_effort", "low")
            return extra, max_tokens + 256
        if lm.startswith("groq/") and "qwen" in lm:
            extra = dict(extra)
            eb = dict(extra.get("extra_body") or {})
            eb.setdefault("reasoning_effort", "none")
            extra["extra_body"] = eb
            return extra, max_tokens + 256
        return extra, max_tokens

    async def preflight(self, timeout: float = 10.0) -> List[Dict[str, Any]]:
        models: List[str] = []
        for role in Role:
            for m in self.chain(role)[:2]:
                if m and m not in models:
                    models.append(m)
        models = models[:10]

        async def probe(m: str) -> Dict[str, Any]:
            t0 = self._clock()
            try:
                extras, tokens = self._model_tuning(m, {}, 16)
                await self._one(m, [{"role": "user", "content": "ping"}], tokens, 0.0, timeout, extras)
                return {"model": m, "ok": True, "secs": round(self._clock() - t0, 2), "note": ""}
            except asyncio.CancelledError:
                raise
            except Exception as e:
                kind = classify_error(e)
                if kind == "fatal":
                    self._fail(m, kind, _short_err(e))
                ok = kind != "fatal" and "empty completion" in str(e)
                return {"model": m, "ok": ok, "secs": round(self._clock() - t0, 2),
                        "note": f"{kind}: {_short_err(e)}"}

        res = await asyncio.gather(*(probe(m) for m in models))
        for r in res:
            if r["ok"]:
                log.info(f"preflight OK   {r['model']} ({r['secs']}s)")
            else:
                log.error(f"preflight FAIL {r['model']} → {r['note']}")
        return list(res)

    async def _one(self, model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float,
                   timeout: float, extra: Dict[str, Any]) -> str:
        async with self._sem:
            text = await asyncio.wait_for(
                self._fn(model=model, messages=messages, max_tokens=max_tokens,
                         temperature=temperature, timeout=timeout, **extra),
                timeout=timeout + 0.5,
            )
        text = strip_reasoning(text or "")
        if not text.strip():
            raise ValueError("empty completion")
        return text.strip()

    @staticmethod
    async def _litellm_completion(*, model: str, messages: List[Dict[str, str]], max_tokens: int,
                                  temperature: float, timeout: float, **extra: Any) -> str:
        import litellm
        litellm.suppress_debug_info = True
        resp = await litellm.acompletion(model=model, messages=messages, max_tokens=max_tokens,
                                         temperature=temperature, timeout=timeout, num_retries=0,
                                         drop_params=True, **extra)
        try:
            return resp.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            return ""

    _REASON = re.compile(r"(احسب|حل\s|معادل|برمج|كود|code|python|bug|error|خطا|قارن|compare|explain|اشرح|"
                         r"step by step|خطوة|ليش|لماذا|why|how does|كيف يعمل|algorithm|sql|regex|prove|اثبت)", re.I)
    _MATH = re.compile(r"\d\s*[\+\-\*/×÷^=]\s*\d")
    _SMALLTALK = re.compile(r"(^|\s)(هلا|هلو|هلاو|الو|مرحبا|اهلا|اهلين|سلام|السلام|صباح|مساء|شلونك|شلونج|شخبارك|كيفك|"
                            r"شكرا|تسلم|يسلمو|عاشت|تمام|زين|اوكي|باي|hi|hello|hey|thanks|thx|ok|okay|bye|salam|"
                            r"good morning|good night|bonjour|salut|merci|hola|gracias|merhaba)(\s|$|[!.،,?؟])", re.I)

    def pick_role(self, text: str, *, has_search: bool = False) -> Role:
        t = (text or "").strip()
        n = len(t)
        if self._MATH.search(t) or (n > 60 and self._REASON.search(t)) or n > 700:
            return Role.REASONING
        if n <= 24 and not has_search and not self._REASON.search(t) and self._SMALLTALK.search(t):
            return Role.FAST
        return Role.CHAT

    def status(self) -> Dict[str, Any]:
        now = self._clock()
        models: Dict[str, Any] = {}
        for role in Role:
            models[role.value] = self.chain(role)
        breakers = {
            m: {"open": b.open_until > now, "retry_in": max(0, round(b.open_until - now)), "failures": b.failures,
                "ok": b.total_ok, "fail": b.total_fail, "last_error": b.last_error}
            for m, b in self._breakers.items()
        }
        return {"chains": models, "breakers": breakers, "stats": dict(self.stats),
                "last_used": dict(self.last_model_used)}

    def all_models(self) -> List[str]:
        seen: List[str] = []
        for role in Role:
            for m in self.chain(role):
                if m not in seen:
                    seen.append(m)
        return seen

    def availability(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            import litellm
        except Exception as e:
            return [{"model": m, "ok": False, "note": f"litellm not importable ({type(e).__name__})"} for m in self.all_models()]
        for m in self.all_models():
            row: Dict[str, Any] = {"model": m, "ok": True, "note": "ok"}
            try:
                litellm.get_llm_provider(model=m)
            except Exception:
                row.update(ok=False, note="not recognised by LiteLLM")
                out.append(row)
                continue
            try:
                env = litellm.validate_environment(model=m)
                missing = env.get("missing_keys") or []
                if missing:
                    row.update(ok=False, note="missing env: " + ",".join(missing))
            except Exception:
                row["note"] = "provider ok (key not verified)"
            out.append(row)
        return out


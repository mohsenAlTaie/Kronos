from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def _raw(name: str) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    return v if v != "" else None


def _str(name: str, default: str = "", *aliases: str) -> str:
    for n in (name, *aliases):
        v = _raw(n)
        if v is not None:
            return v
    return default


def _bool(name: str, default: bool, *aliases: str) -> bool:
    for n in (name, *aliases):
        v = _raw(n)
        if v is not None:
            return v.lower() in ("1", "true", "yes", "on", "y")
    return default


def _int(name: str, default: int, *aliases: str, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    val = default
    for n in (name, *aliases):
        v = _raw(n)
        if v is not None:
            try:
                val = int(float(v))
            except ValueError:
                val = default
            break
    if lo is not None:
        val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val


def _float(name: str, default: float, *aliases: str, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    val = default
    for n in (name, *aliases):
        v = _raw(n)
        if v is not None:
            try:
                val = float(v)
            except ValueError:
                val = default
            break
    if lo is not None:
        val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val


def _csv(name: str, *aliases: str) -> List[str]:
    for n in (name, *aliases):
        v = _raw(n)
        if v is not None:
            return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _int_list(name: str) -> List[int]:
    out: List[int] = []
    for x in _csv(name):
        try:
            out.append(int(x))
        except ValueError:
            continue
    return out


def _json_dict(name: str) -> Dict:
    v = _raw(name)
    if not v:
        return {}
    try:
        d = json.loads(v)
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


ROLES = ("chat", "fast", "reasoning", "moderation", "summary", "vision", "safeguard")


@dataclass
class Settings:
    api_id: int = 0
    api_hash: str = ""
    session_string: str = ""
    owner_id: int = 0
    device_model: str = "Wispbyte"

    tz: str = "Asia/Baghdad"
    time_format: str = "12"
    update_interval: int = 60
    bio_interval_minutes: int = 10
    bio_bots: str = "@Musicliveprobot @NovaGddbot"

    primary_chat_model: str = ""
    fast_chat_model: str = ""
    reasoning_model: str = ""
    moderation_model: str = ""
    summary_model: str = ""
    vision_model: str = ""
    safeguard_model: str = ""
    vision_fallbacks: List[str] = field(default_factory=list)
    role_fallbacks: Dict[str, List[str]] = field(default_factory=dict)
    global_fallbacks: List[str] = field(default_factory=list)
    allow_coder_models: bool = False
    chat_extra_params: Dict = field(default_factory=dict)

    ai_timeout: float = 8.0
    max_ai_latency: float = 15.0
    moderation_timeout: float = 3.5
    summary_timeout: float = 25.0
    ai_max_retries: int = 1
    chat_max_tokens: int = 350
    chat_temperature: float = 0.7
    breaker_threshold: int = 3
    breaker_cooldown: float = 45.0
    max_concurrent_ai: int = 4

    fast_reply: bool = True
    max_typing_delay: float = 1.2
    coalesce_window: float = 1.2
    coalesce_max_wait: float = 3.0

    max_input_chars: int = 1500
    max_reply_chars: int = 1200
    max_context_messages: int = 14
    context_token_budget: int = 1500
    memory_ttl_days: int = 14
    memory_hard_ttl_days: int = 120
    archive_batch: int = 12
    profile_touch_seconds: float = 300.0
    fact_ttl_days: int = 180
    cleanup_interval_hours: int = 12
    enable_summarization: bool = True
    memory_top_k: int = 4
    memory_context_chars: int = 600
    summary_max_chars: int = 500
    rate_limit_per_minute: int = 20
    dedup_ttl: float = 600.0
    max_pending_users: int = 200

    web_search_provider: str = "brave"
    web_search_max_results: int = 5
    enable_auto_search: bool = True
    search_timeout: float = 6.0
    search_cache_ttl: float = 300.0

    vision_enabled: bool = True
    image_moderation: bool = True
    image_moderation_timeout: float = 14.0
    image_confirm: bool = True
    image_moderation_max_wait: float = 60.0
    vision_tpm_budget: int = 7000
    vision_direct_reply: bool = False
    vision_timeout: float = 25.0
    max_image_bytes: int = 3_000_000
    delete_chat_on_block: bool = True
    delete_chat_revoke: bool = True
    block_delete_delay: float = 3.0

    trust_contacts: bool = True
    trust_engaged_hours: float = 24.0

    instant_block_nsfw: bool = True
    instant_block_labels: List[str] = field(default_factory=lambda: ["THREAT", "SEXUAL_ABUSE"])
    instant_block_confidence: float = 0.90
    image_strict: bool = True
    video_moderation: bool = True
    video_frames: int = 3
    video_max_bytes: int = 25_000_000
    video_max_seconds: int = 600
    moderate_edits: bool = True
    repeat_offender_strict: bool = True
    notify_moderation: bool = True

    hold_replies: bool = True
    hold_minutes: float = 10.0

    stickers_enabled: bool = True
    sticker_cooldown: float = 20.0
    sticker_source: str = "mine"
    sticker_max_sets: int = 60
    sticker_cache_hours: float = 6.0
    control_bot_token: str = ""
    button_styles: bool = True
    control_page_size: int = 5

    voice_enabled: bool = True
    stt_model: str = "groq/whisper-large-v3-turbo"
    stt_fallback_model: str = "groq/whisper-large-v3"
    stt_language: str = ""
    stt_timeout: float = 25.0
    voice_max_seconds: int = 120
    voice_max_bytes: int = 5_000_000
    stt_hourly_seconds: int = 6000

    moderation_enabled: bool = True
    warning_ttl_hours: float = 24.0
    max_warnings: int = 2
    moderation_confidence: float = 0.8
    moderation_llm_mode: str = "smart"
    moderation_confirm: bool = True
    moderation_web_lookup: bool = True
    learn_promote_hits: int = 2

    smart_presence_enabled: bool = True
    owner_idle_timeout: float = 15.0
    presence_grace_seconds: float = 6.0
    owner_status_check_interval: float = 10.0
    owner_status_poll: bool = True
    owner_status_ttl: float = 300.0
    owner_activity_signals: bool = True
    owner_chat_cooldown: float = 90.0

    ai_reply_enabled: bool = True
    draft_mode: bool = False
    whitelist: List[int] = field(default_factory=list)
    reply_in_groups: bool = False
    media_only_reply: bool = False
    failure_reply_mode: str = "short"
    include_sender_bio: bool = True

    data_dir: str = "data"
    db_path: str = ""
    persona_file: str = "persona.json"
    log_level: str = "INFO"
    log_message_content: bool = False
    max_flood_wait: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.api_id = _int("API_ID", 0)
        s.api_hash = _str("API_HASH")
        s.session_string = _str("SESSION_STRING")
        s.owner_id = _int("OWNER_ID", 0)
        s.device_model = _str("TG_DEVICE_MODEL", "Wispbyte")

        s.tz = _str("TZ_NAME", "Asia/Baghdad")
        s.time_format = _str("TIME_FORMAT", "12")
        s.update_interval = _int("UPDATE_INTERVAL", 60, lo=15)
        s.bio_interval_minutes = _int("BIO_UPDATE_INTERVAL_MINUTES", 10, lo=1)
        s.bio_bots = _str("BIO_BOTS", s.bio_bots)

        s.primary_chat_model = _str("PRIMARY_CHAT_MODEL", "", "DEFAULT_MODEL")
        s.fast_chat_model = _str("FAST_CHAT_MODEL", s.primary_chat_model)
        s.reasoning_model = _str("REASONING_MODEL", s.primary_chat_model)
        s.moderation_model = _str("MODERATION_MODEL", s.fast_chat_model)
        s.summary_model = _str("SUMMARY_MODEL", s.fast_chat_model)
        s.vision_model = _str("VISION_MODEL", "groq/qwen/qwen3.8-27b")
        s.safeguard_model = _str("SAFEGUARD_MODEL", "groq/openai/gpt-oss-safeguard-20b")
        s.vision_fallbacks = _csv("VISION_FALLBACK_MODELS")
        s.trust_contacts = _bool("TRUST_CONTACTS", True)
        s.trust_engaged_hours = _float("TRUST_ENGAGED_HOURS", 24.0, lo=0.0, hi=720.0)
        s.instant_block_nsfw = _bool("INSTANT_BLOCK_NSFW", True)
        labs = [x.upper() for x in _csv("INSTANT_BLOCK_LABELS")] if _raw("INSTANT_BLOCK_LABELS") is not None else None
        valid = {"INSULT", "PROFANITY", "THREAT", "HARASSMENT", "SEXUAL_ABUSE"}
        s.instant_block_labels = [x for x in labs if x in valid] if labs is not None else ["THREAT", "SEXUAL_ABUSE"]
        s.instant_block_confidence = _float("INSTANT_BLOCK_CONFIDENCE", 0.90, lo=0.5, hi=0.99)
        s.image_strict = _bool("IMAGE_STRICT", True)
        s.video_moderation = _bool("VIDEO_MODERATION", True)
        s.video_frames = _int("VIDEO_FRAMES", 3, lo=1, hi=6)
        s.video_max_bytes = _int("VIDEO_MAX_BYTES", 25_000_000, lo=500_000, hi=200_000_000)
        s.video_max_seconds = _int("VIDEO_MAX_SECONDS", 600, lo=5, hi=7200)
        s.moderate_edits = _bool("MODERATE_EDITS", True)
        s.repeat_offender_strict = _bool("REPEAT_OFFENDER_STRICT", True)
        s.notify_moderation = _bool("NOTIFY_OWNER_ON_MODERATION", True)
        s.button_styles = _bool("BUTTON_STYLES", True)
        s.control_page_size = _int("CONTROL_PAGE_SIZE", 5, lo=3, hi=8)
        s.hold_replies = _bool("HOLD_REPLIES", True)
        s.hold_minutes = _float("HOLD_MINUTES", 10.0, lo=0.5, hi=60.0)
        s.stickers_enabled = _bool("STICKERS_ENABLED", True)
        s.sticker_cooldown = _float("STICKER_COOLDOWN", 20.0, lo=0.0, hi=3600.0)
        src = _str("STICKER_SOURCE", "mine").lower()
        s.sticker_source = src if src in ("mine", "any", "search") else "mine"
        s.sticker_max_sets = _int("STICKER_MAX_SETS", 60, lo=1, hi=300)
        s.sticker_cache_hours = _float("STICKER_CACHE_HOURS", 6.0, lo=0.1, hi=168.0)
        s.control_bot_token = _str("CONTROL_BOT_TOKEN", "")
        s.voice_enabled = _bool("VOICE_ENABLED", True)
        s.stt_model = _str("STT_MODEL", "groq/whisper-large-v3-turbo")
        s.stt_fallback_model = _str("STT_FALLBACK_MODEL", "groq/whisper-large-v3")
        s.stt_language = _str("STT_LANGUAGE", "").lower()
        s.stt_timeout = _float("STT_TIMEOUT", 25.0, lo=3.0)
        s.voice_max_seconds = _int("VOICE_MAX_SECONDS", 120, lo=5, hi=900)
        s.voice_max_bytes = _int("VOICE_MAX_BYTES", 5_000_000, lo=50_000, hi=25_000_000)
        s.stt_hourly_seconds = _int("STT_HOURLY_SECONDS", 6000, lo=60, hi=1_000_000)
        s.image_confirm = _bool("IMAGE_CONFIRM", True)
        s.image_moderation = _bool("IMAGE_MODERATION", True)
        s.image_moderation_timeout = _float("IMAGE_MODERATION_TIMEOUT", 14.0, lo=3.0)
        s.image_moderation_max_wait = _float("IMAGE_MODERATION_MAX_WAIT", 60.0, lo=5.0, hi=300.0)
        s.vision_tpm_budget = _int("VISION_TPM_BUDGET", 7000, lo=1000, hi=1_000_000)
        s.vision_direct_reply = _bool("VISION_DIRECT_REPLY", False)
        s.vision_enabled = _bool("VISION_ENABLED", True)
        s.vision_timeout = _float("VISION_TIMEOUT", 25.0, lo=3.0)
        s.max_image_bytes = _int("MAX_IMAGE_BYTES", 3_000_000, lo=50_000, hi=15_000_000)
        s.delete_chat_on_block = _bool("DELETE_CHAT_ON_BLOCK", True)
        s.delete_chat_revoke = _bool("DELETE_CHAT_FOR_BOTH", True)
        s.block_delete_delay = _float("BLOCK_DELETE_DELAY", 3.0, lo=0.0, hi=60.0)
        s.global_fallbacks = _csv("FALLBACK_MODELS") or [
            m for m in (_str("FALLBACK_MODEL_1"), _str("FALLBACK_MODEL_2")) if m
        ]
        for role in ROLES:
            fb = _csv(f"{role.upper()}_FALLBACKS")
            if fb:
                s.role_fallbacks[role] = fb
        s.allow_coder_models = _bool("ALLOW_CODER_MODELS", False)
        s.chat_extra_params = _json_dict("CHAT_EXTRA_PARAMS")

        s.ai_timeout = _float("AI_TIMEOUT", 8.0, lo=1.0)
        s.max_ai_latency = _float("MAX_AI_LATENCY", 15.0, lo=2.0)
        s.moderation_timeout = _float("MODERATION_TIMEOUT", 3.5, lo=0.5)
        s.summary_timeout = _float("SUMMARY_TIMEOUT", 25.0, lo=3.0)
        s.ai_max_retries = _int("AI_MAX_RETRIES", 1, lo=0, hi=3)
        s.chat_max_tokens = _int("CHAT_MAX_TOKENS", 350, lo=32, hi=4000)
        s.chat_temperature = _float("CHAT_TEMPERATURE", 0.7, lo=0.0, hi=1.5)
        s.breaker_threshold = _int("BREAKER_THRESHOLD", 3, lo=1)
        s.breaker_cooldown = _float("BREAKER_COOLDOWN", 45.0, lo=5.0)
        s.max_concurrent_ai = _int("MAX_CONCURRENT_AI", 4, lo=1, hi=32)

        s.fast_reply = _bool("FAST_REPLY", True)
        s.max_typing_delay = _float("MAX_TYPING_DELAY", 1.2, lo=0.0, hi=10.0)
        s.coalesce_window = _int("COALESCE_WINDOW_MS", 1200, lo=0, hi=10000) / 1000.0
        s.coalesce_max_wait = _int("COALESCE_MAX_WAIT_MS", 3000, lo=0, hi=20000) / 1000.0

        s.max_input_chars = _int("MAX_INPUT_CHARS", 1500, lo=100)
        s.max_reply_chars = _int("MAX_REPLY_CHARS", 1200, lo=80, hi=4000)
        s.max_context_messages = _int("MAX_CONTEXT_MESSAGES", 14, "MAX_CONTEXT", lo=2, hi=40)
        s.context_token_budget = _int("CONTEXT_TOKEN_BUDGET", 1500, lo=200)
        s.memory_ttl_days = _int("MEMORY_TTL_DAYS", 14, lo=1)
        s.memory_hard_ttl_days = _int("MEMORY_HARD_TTL_DAYS", 120, lo=s.memory_ttl_days, hi=3650)
        s.archive_batch = _int("ARCHIVE_BATCH", 12, lo=1, hi=100)
        s.profile_touch_seconds = _float("PROFILE_TOUCH_SECONDS", 300.0, lo=0.0, hi=86400.0)
        s.fact_ttl_days = _int("FACT_TTL_DAYS", 180, lo=1)
        s.cleanup_interval_hours = _int("CLEANUP_INTERVAL_HOURS", 12, lo=1)
        s.enable_summarization = _bool("ENABLE_SUMMARIZATION", True)
        s.memory_top_k = _int("MEMORY_TOP_K", 4, lo=0, hi=12)
        s.memory_context_chars = _int("MEMORY_CONTEXT_CHARS", 600, lo=100)
        s.summary_max_chars = _int("SUMMARY_MAX_CHARS", 500, lo=100)
        s.rate_limit_per_minute = _int("RATE_LIMIT_PER_MINUTE", 20, lo=1)
        s.dedup_ttl = _float("DEDUP_TTL_SECONDS", 600.0, lo=10.0)
        s.max_pending_users = _int("MAX_PENDING_USERS", 200, lo=10)

        s.web_search_provider = _str("WEB_SEARCH_PROVIDER", "brave")
        s.web_search_max_results = _int("WEB_SEARCH_MAX_RESULTS", 5, lo=1, hi=10)
        s.enable_auto_search = _bool("ENABLE_AUTO_SEARCH", True)
        s.search_timeout = _float("SEARCH_TIMEOUT", 6.0, lo=1.0)
        s.search_cache_ttl = _float("SEARCH_CACHE_TTL", 300.0, lo=0.0)

        s.moderation_enabled = _bool("MODERATION_ENABLED", True, "ENABLE_AUTO_MODERATION")
        s.warning_ttl_hours = _float("WARNING_TTL_HOURS", 24.0, lo=0.01)
        s.max_warnings = _int("MAX_WARNINGS", 2, lo=1, hi=10)
        s.moderation_confidence = _float("MODERATION_CONFIDENCE", 0.8, lo=0.5, hi=0.99)
        mode = _str("MODERATION_LLM_MODE", "smart").lower()
        s.moderation_llm_mode = mode if mode in ("all", "off") else "smart"
        s.moderation_confirm = _bool("MODERATION_CONFIRM", True)
        s.moderation_web_lookup = _bool("MODERATION_WEB_LOOKUP", True)
        s.learn_promote_hits = _int("TERM_LEARN_HITS", 2, lo=1, hi=10)

        s.smart_presence_enabled = _bool("SMART_PRESENCE_ENABLED", True)
        s.owner_idle_timeout = _float("OWNER_IDLE_TIMEOUT", 15.0, lo=0.0)
        s.presence_grace_seconds = _float("PRESENCE_GRACE_SECONDS", 6.0, lo=0.0)
        s.owner_status_check_interval = _float("OWNER_STATUS_CHECK_INTERVAL", 10.0, lo=5.0)
        s.owner_status_poll = _bool("OWNER_STATUS_POLL", True)
        s.owner_status_ttl = _float("OWNER_STATUS_TTL", 300.0, lo=10.0, hi=900.0)
        s.owner_activity_signals = _bool("OWNER_ACTIVITY_SIGNALS", True)
        s.owner_chat_cooldown = _float("OWNER_CHAT_COOLDOWN", 90.0, lo=0.0)

        s.ai_reply_enabled = _bool("AI_REPLY_ENABLED", True)
        s.draft_mode = _bool("DRAFT_MODE", False)
        s.whitelist = _int_list("WHITELIST_USERS")
        s.reply_in_groups = _bool("REPLY_IN_GROUPS", False)
        s.media_only_reply = _bool("MEDIA_ONLY_REPLY", False)
        mode = _str("FAILURE_REPLY_MODE", "short").lower()
        s.failure_reply_mode = mode if mode in ("short", "silent") else "short"
        s.include_sender_bio = _bool("INCLUDE_SENDER_BIO", True)

        s.data_dir = _str("DATA_DIR", "data")
        s.db_path = _str("DB_PATH", os.path.join(s.data_dir, "ai_memory.db"))
        s.persona_file = _str("PERSONA_FILE", "persona.json")
        s.log_level = _str("LOG_LEVEL", "INFO").upper()
        s.log_message_content = _bool("LOG_MESSAGE_CONTENT", False)
        s.max_flood_wait = _int("MAX_FLOOD_WAIT", 60, lo=1)
        return s

    def validate(self) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        if not self.api_id or not self.api_hash:
            out.append(("fatal", "API_ID / API_HASH are missing"))
        if not self.session_string:
            out.append(("fatal", "SESSION_STRING is missing (run generate_session.py locally)"))
        if not self.owner_id:
            out.append(("fatal", "OWNER_ID is missing"))
        if not self.primary_chat_model:
            out.append(("fatal", "PRIMARY_CHAT_MODEL (or legacy DEFAULT_MODEL) is not set"))
        if self.max_warnings < 1:
            out.append(("warn", "MAX_WARNINGS < 1 makes no sense, clamped to 1"))
        if self.coalesce_max_wait < self.coalesce_window:
            out.append(("warn", "COALESCE_MAX_WAIT_MS < COALESCE_WINDOW_MS; window wins"))
        return out

    def presence_params(self) -> Dict[str, float]:
        wake = self.owner_idle_timeout
        return {"grace_period": max(self.presence_grace_seconds, wake), "idle_hold": wake, "status_ttl": self.owner_status_ttl,
                "chat_cooldown": min(self.owner_chat_cooldown, wake) if self.owner_chat_cooldown > 0 else 0.0}

    def secrets(self) -> List[str]:
        vals = [self.api_hash, self.session_string]
        for k, v in os.environ.items():
            ku = k.upper()
            if ku.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_HASH", "_PASSWORD")) or ku == "SESSION_STRING":
                if v and len(v) >= 8:
                    vals.append(v)
        return sorted({v for v in vals if v}, key=len, reverse=True)

    def model_for(self, role: str) -> str:
        return {
            "chat": self.primary_chat_model,
            "fast": self.fast_chat_model,
            "reasoning": self.reasoning_model,
            "moderation": self.moderation_model,
            "summary": self.summary_model,
            "vision": self.vision_model,
            "safeguard": self.safeguard_model,
        }[role]

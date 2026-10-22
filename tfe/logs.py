from __future__ import annotations

import logging
import re
from typing import Iterable, List

_PATTERNS = [
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}"),
    re.compile(r"\b1[A-Za-z0-9_\-+/=]{200,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|bearer)([\"'=:\s]+)[A-Za-z0-9_\-\.]{16,}"),
]


def scrub(text: str, secrets: Iterable[str] = ()) -> str:
    for s in secrets:
        if s and s in text:
            text = text.replace(s, "***")
    for p in _PATTERNS:
        if p.groups >= 2:
            text = p.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
        else:
            text = p.sub("***", text)
    return text


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, datefmt: str, secrets: List[str]):
        super().__init__(fmt, datefmt)
        self._secrets = [s for s in secrets if s and len(s) >= 8]

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record), self._secrets)


def setup_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(
        RedactingFormatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S", list(secrets))
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for name in ("LiteLLM", "litellm", "httpx", "httpcore", "telethon", "apscheduler"):
        logging.getLogger(name).setLevel(logging.WARNING)


class TagLogger(logging.LoggerAdapter):
    def __init__(self, tag: str):
        super().__init__(logging.getLogger(f"tfe.{tag.lower()}"), {})
        self.tag = tag

    def process(self, msg, kwargs):
        return f"[{self.tag}] {msg}", kwargs


AI = TagLogger("AI")
MOD = TagLogger("MODERATION")
PRESENCE = TagLogger("PRESENCE")
MEMORY = TagLogger("MEMORY")
SEARCH = TagLogger("SEARCH")
TELEGRAM = TagLogger("TELEGRAM")
ERROR = TagLogger("ERROR")

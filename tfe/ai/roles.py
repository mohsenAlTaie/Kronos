from enum import Enum


class Role(str, Enum):
    CHAT = "chat"
    FAST = "fast"
    REASONING = "reasoning"
    MODERATION = "moderation"
    SUMMARY = "summary"
    VISION = "vision"
    SAFEGUARD = "safeguard"

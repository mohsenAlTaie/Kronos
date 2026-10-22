from __future__ import annotations

import re
import unicodedata
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Hashable, List, Optional

TG_MAX_LEN = 4000


class TTLCache:

    def __init__(self, maxsize: int = 1024, ttl: float = 600.0, clock: Callable[[], float] = time.monotonic):
        self.maxsize = maxsize
        self.ttl = ttl
        self._clock = clock
        self._d: "OrderedDict[Hashable, tuple]" = OrderedDict()

    def _purge_key(self, key: Hashable) -> None:
        self._d.pop(key, None)

    def get(self, key: Hashable, default=None):
        item = self._d.get(key)
        if item is None:
            return default
        exp, val = item
        if exp < self._clock():
            self._purge_key(key)
            return default
        self._d.move_to_end(key)
        return val

    def set(self, key: Hashable, value, ttl: Optional[float] = None) -> None:
        self._d[key] = (self._clock() + (self.ttl if ttl is None else ttl), value)
        self._d.move_to_end(key)
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)

    def add_if_new(self, key: Hashable) -> bool:
        if self.get(key, None) is not None:
            return False
        self.set(key, True)
        return True

    def __contains__(self, key: Hashable) -> bool:
        return self.get(key, None) is not None

    def __len__(self) -> int:
        return len(self._d)

    def discard(self, key: Hashable) -> None:
        self._d.pop(key, None)

    def clear(self) -> None:
        self._d.clear()


class SlidingWindowLimiter:

    def __init__(self, limit: int, window: float = 60.0, maxkeys: int = 5000,
                 clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.maxkeys, self._clock = limit, window, maxkeys, clock
        self._d: "OrderedDict[Hashable, Deque[float]]" = OrderedDict()

    def allow(self, key: Hashable) -> bool:
        now = self._clock()
        q = self._d.get(key)
        if q is None:
            q = deque()
            self._d[key] = q
        self._d.move_to_end(key)
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(self._d) > self.maxkeys:
            self._d.popitem(last=False)
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_FENCE = re.compile(r"<<<|>>>|\[\[TFE[^\]]*\]\]", re.I)
_MULTI_NL = re.compile(r"\n{3,}")


def sanitize_user_text(text: str, max_chars: int) -> str:
    if not text:
        return ""
    text = _CTRL.sub("", text)
    text = _FENCE.sub(" ", text)
    text = _MULTI_NL.sub("\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def clean_markdown(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"```.*?```", lambda m: m.group(0).strip("`"), text, flags=re.S)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.S)
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", text, flags=re.S)
    text = re.sub(r"`(.+?)`", r"\1", text, flags=re.S)
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.S)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.M)
    text = text.replace("**", "").replace("`", "")
    return text.strip()


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_reasoning(text: str) -> str:
    text = _THINK.sub("", text or "")
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def split_message(text: str, limit: int = TG_MAX_LEN) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks: List[str] = []
    while len(text) > limit:
        cut = -1
        for sep in ("\n\n", "\n", "۔", ". ", "؟ ", "? ", "! ", "، ", ", ", " "):
            i = text.rfind(sep, 0, limit)
            if i >= limit * 0.4:
                cut = i + len(sep)
                break
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return [c for c in chunks if c]


def shorten(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    for sep in ("\n", ". ", "؟ ", "? ", "! ", "، ", ", "):
        i = window.rfind(sep)
        if i >= max_chars * 0.5:
            return window[: i + (0 if sep == "\n" else 1)].strip()
    i = window.rfind(" ")
    return (window[:i] if i > max_chars * 0.5 else window).strip() + "…"


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3 + 1)


_AR = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_LAT = re.compile(r"[A-Za-z]")
_IRAQI = {
    "شلونك", "شلونكم", "شلونج", "شلون", "شنو", "شكو", "ماكو", "اكو", "هسه", "هسة", "وين", "وينك",
    "شكد", "لك", "ولك", "يمعود", "زين", "اني", "هاي", "هذا", "ايوه", "دا", "هواي", "كلش", "خوش",
    "چا", "شبيك", "شبيج", "عاشت", "يابه", "اخوي", "خالتي", "حبيبي", "لعد", "لعب", "گلت", "گال",
    "چان", "چنت", "ديروح", "شوكت", "منو", "مو", "بعد", "هالكد", "ريـت", "اريد", "ابي", "دگ",
}
_IRAQI_STRONG = {"شلونك", "شنو", "شكو", "ماكو", "اكو", "هسه", "هسة", "كلش", "خوش", "هواي", "شكد", "يمعود",
                 "شبيك", "شلونج", "لعد", "چان", "گلت", "منو", "شوكت", "دگ", "هالكد", "شلونكم", "وينك"}
_ARABIZI = {
    "shlonak", "shlonek", "shlon", "shlonik", "shnu", "shno", "shaku", "maku", "haku", "hassa", "hasa",
    "wain", "wein", "wenak", "habibi", "7abibi", "3ad", "ya3ni", "yaeni", "inshallah", "zain", "zeen",
    "kulesh", "klesh", "hala", "halo", "ahlan", "marhaba", "shukran", "mashi", "tamam", "3ashat",
}
_GULF = {"وش", "ايش", "شخبارك", "ابغى", "ابي", "زين", "حيالله", "يالله"}
_EGY = {"ازيك", "ايه", "دلوقتي", "كده", "عايز", "عايزه", "اوي", "ده", "دي", "ايوة"}
_LEV = {"كيفك", "شو", "هلق", "هيك", "منيح", "بدي", "شوفي", "يا زلمة", "كتير"}
_TOKEN = re.compile(r"[^\W_]+", re.U)
_NON_ARABIC_ARABIC_SCRIPT = re.compile(r"[\u067E\u0698\u06A9\u06CC\u06D5\u0695\u06B5\u06CE\u06C6\u06A4\u06BE\u0679\u0688\u0691\u06BA\u06D2\u06C1]")
_LATIN_ACCENTED = re.compile(r"[\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]")
_EN_COMMON = {
    "the", "a", "an", "is", "are", "am", "was", "were", "be", "i", "you", "he", "she", "it", "we", "they", "my",
    "your", "me", "what", "who", "why", "how", "when", "where", "do", "does", "did", "can", "could", "will",
    "would", "should", "hi", "hello", "hey", "thanks", "thank", "please", "yes", "no", "ok", "okay", "good",
    "morning", "night", "bro", "man", "dude", "this", "that", "and", "or", "but", "to", "of", "in", "on", "for",
    "with", "not", "have", "has", "know", "want", "need", "like", "love", "see", "tell", "say", "go", "come",
    "get", "just", "so", "if", "its", "im", "u", "ur", "ya", "lol", "bye", "sorry", "help", "price", "time",
}


@dataclass(frozen=True)
class LangHint:
    script: str
    dialect: str
    reply_lang: str

    def describe(self) -> str:
        names = {
            "iraqi": "لهجة عراقية", "gulf": "لهجة خليجية", "egyptian": "لهجة مصرية",
            "levantine": "لهجة شامية", "msa": "عربية فصحى/بسيطة", "arabizi": "عربيزي (عربي بحروف لاتينية)",
            "english": "English", "other": "لغة ثانية (مو عربي/إنجليزي واضح)", "unknown": "غير واضحة",
        }
        return names.get(self.dialect, "غير واضحة")


def detect_lang(text: str) -> LangHint:
    text = text or ""
    ar = len(_AR.findall(text))
    lat = len(_LAT.findall(text))
    toks = [t.lower() for t in _TOKEN.findall(text)]
    tokset = set(toks)
    if ar == 0 and lat == 0:
        return LangHint("other", "unknown", "auto")
    if ar and _NON_ARABIC_ARABIC_SCRIPT.search(text):
        return LangHint("other", "other", "auto")
    arabizi_hits = len(tokset & _ARABIZI)
    if lat > ar:
        digit_mix = re.search(r"[a-z][2356789]|[2356789][a-z]", text.lower())
        if arabizi_hits >= 1 or digit_mix:
            return LangHint("en", "arabizi", "ar-iraqi")
        if _LATIN_ACCENTED.search(text) or not (tokset & _EN_COMMON):
            return LangHint("other", "other", "auto")
        return LangHint("en", "english", "en")
    script = "mixed" if lat >= 3 and ar >= 3 else "ar"
    if (tokset & _IRAQI_STRONG) or re.search(r"[گچ]", text):
        return LangHint(script, "iraqi", "ar-iraqi")
    if len(tokset & _EGY) >= 1:
        return LangHint(script, "egyptian", "ar-iraqi")
    if len(tokset & _LEV) >= 1:
        return LangHint(script, "levantine", "ar-iraqi")
    if len(tokset & _GULF) >= 1:
        return LangHint(script, "gulf", "ar-iraqi")
    if len(tokset & _IRAQI) >= 2:
        return LangHint(script, "iraqi", "ar-iraqi")
    return LangHint(script, "msa", "ar-iraqi")


def detect_lang_ctx(text: str, history_user_texts: "list[str] | None" = None) -> LangHint:
    lang = detect_lang(text)
    if lang.dialect != "unknown":
        return lang
    for t in reversed(history_user_texts or []):
        l2 = detect_lang(t)
        if l2.dialect != "unknown":
            return l2
    return LangHint("ar", "iraqi", "ar-iraqi")


_CJK = re.compile(r"[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]+")


def strip_foreign_cjk(reply: str, *context: str) -> str:
    if not reply or not _CJK.search(reply):
        return reply
    if any(c and _CJK.search(c) for c in context):
        return reply
    out = _CJK.sub("", reply)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([?!؟.,،:;])", r"\1", out)
    return out.strip(" \n\t-–—:،,؛").lstrip(".?!؟ ")


_SMALL_CAPS = str.maketrans({
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f", "ɢ": "g", "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k",
    "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ǫ": "q", "ʀ": "r", "ꜱ": "s", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v",
    "ᴡ": "w", "ʏ": "y", "ᴢ": "z", "ᵃ": "a", "ᵇ": "b", "ᶜ": "c", "ᵈ": "d", "ᵉ": "e", "ᶠ": "f", "ᵍ": "g", "ʰ": "h",
    "ⁱ": "i", "ʲ": "j", "ᵏ": "k", "ˡ": "l", "ᵐ": "m", "ⁿ": "n", "ᵒ": "o", "ᵖ": "p", "ʳ": "r", "ˢ": "s", "ᵗ": "t",
    "ᵘ": "u", "ᵛ": "v", "ʷ": "w", "ˣ": "x", "ʸ": "y", "ᶻ": "z",
})
_BLANKS = {"\u3164", "\u115f", "\u1160", "\u17b4", "\u17b5", "\u2800", "\u0640", "\uffa0"}


def clean_display_name(raw: str) -> "tuple[str, bool]":
    t = unicodedata.normalize("NFKC", raw or "").translate(_SMALL_CAPS)
    out = []
    for ch in t:
        if ch in _BLANKS or 0x13000 <= ord(ch) <= 0x1345F:
            continue
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "N") or ch in ".-'_":
            out.append(ch)
        elif cat in ("Zs",) or ch.isspace():
            out.append(" ")
    clean = re.sub(r"\s+", " ", "".join(out)).strip(" ._-'")
    return clean, bool(re.search(r"[^\W\d_]", clean))

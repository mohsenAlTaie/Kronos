from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Tuple

_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad\u180e\u034f\u061c]")

_AR_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ٲ": "ا", "ٳ": "ا",
    "ى": "ي", "ئ": "ي", "ی": "ي", "ې": "ي", "ۍ": "ي", "ێ": "ي", "ے": "ي",
    "ؤ": "و", "ۆ": "و", "ۇ": "و", "ۈ": "و",
    "ة": "ه", "ھ": "ه", "ہ": "ه", "ۃ": "ه", "ە": "ه",
    "گ": "ك", "ڭ": "ك", "چ": "ك", "ک": "ك", "ڪ": "ك", "ګ": "ك",
    "ڤ": "ف", "ڢ": "ف", "ڦ": "ف",
    "پ": "ب", "ڀ": "ب",
    "ژ": "ز", "ڨ": "ق", "ڧ": "ق", "ڕ": "ر", "ړ": "ر",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "ـ": None,
})

_CONFUSABLES = str.maketrans({
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c",
    "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "ө": "o",
    "ο": "o", "ν": "v", "ι": "i", "κ": "k", "ρ": "p", "τ": "t", "α": "a", "β": "b", "ε": "e",
    "η": "n", "υ": "u", "χ": "x", "ϲ": "c", "ı": "i", "ℓ": "l", "ƒ": "f", "ɑ": "a", "ɪ": "i",
    "ӏ": "l", "ǀ": "l", "ʟ": "l", "ᴜ": "u", "ᴋ": "k", "ꜱ": "s", "ᴀ": "a", "ᴇ": "e", "ᴏ": "o",
})

_SYMBOL_LEET = {"@": "a", "$": "s", "!": "i", "|": "l", "€": "e", "£": "l", "¡": "i"}
_SYM_BETWEEN = re.compile(r"(?<=[a-z])([@$!|€£¡])(?=[a-z])")
_SYM_START = re.compile(r"(?<![\w])([@$])(?=[a-z]{2,})")
_DIGIT_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b"})
_TOKEN = re.compile(r"[^\W_]+", re.U)
_RUN2 = re.compile(r"(.)\1{2,}")
_RUN1 = re.compile(r"(.)\1+")


def _is_ar(ch: str) -> bool:
    return "\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f"


def _is_lat(ch: str) -> bool:
    return "a" <= ch <= "z"


def base_normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = text.translate(_AR_FOLD)
    text = text.translate(_CONFUSABLES)
    text = _SYM_BETWEEN.sub(lambda m: _SYMBOL_LEET[m.group(1)], text)
    text = _SYM_START.sub(lambda m: _SYMBOL_LEET[m.group(1)], text)
    return text


def collapse1(s: str) -> str:
    return _RUN1.sub(r"\1", s)


def collapse2(s: str) -> str:
    return _RUN2.sub(r"\1\1", s)


@dataclass(frozen=True)
class Token:
    forms: Tuple[str, ...]
    primary: str = ""
    stripped: Tuple[str, ...] = ()
    voc: Tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "primary", self.forms[0])


_ARTICLES = ("وبال", "وال", "بال", "فال", "كال", "لل", "ال")


def _article_forms(tok: str) -> Tuple[str, ...]:
    if not tok or not _is_ar(tok[0]):
        return ()
    for p in _ARTICLES:
        if tok.startswith(p) and len(tok) - len(p) >= 2:
            rest = tok[len(p):]
            return tuple(dict.fromkeys((rest, collapse1(rest))))
    return ()


_VOCATIVES = ("ياو", "يا")


def _vocative_forms(tok: str) -> Tuple[str, ...]:
    if not tok or not _is_ar(tok[0]):
        return ()
    out: List[str] = []
    for p in _VOCATIVES:
        if tok.startswith(p) and len(tok) - len(p) >= 3:
            rest = tok[len(p):]
            out.extend((rest, collapse1(rest)))
    return tuple(dict.fromkeys(out))


def _forms_for(tok: str) -> Tuple[str, ...]:
    forms: List[str] = [tok]
    has_lat = any(_is_lat(c) for c in tok)
    if has_lat and any(c.isdigit() for c in tok):
        leet = tok.translate(_DIGIT_LEET)
        if leet != tok:
            forms.append(leet)
    for f in list(forms):
        for g in (collapse2(f), collapse1(f)):
            if g not in forms:
                forms.append(g)
    return tuple(forms)


def _split_scripts(tok: str) -> List[str]:
    has_ar = any(_is_ar(c) for c in tok)
    has_lat = any(_is_lat(c) for c in tok)
    if not (has_ar and has_lat):
        return [tok]
    parts: List[str] = []
    cur = ""
    cur_script = ""
    for ch in tok:
        s = "ar" if _is_ar(ch) else "lat" if _is_lat(ch) else cur_script or "lat"
        if cur and s != cur_script:
            parts.append(cur)
            cur = ""
        cur += ch
        cur_script = s
    if cur:
        parts.append(cur)
    return parts


def _script_of(tok: str) -> str:
    if any(_is_ar(c) for c in tok):
        return "ar"
    if any(_is_lat(c) for c in tok):
        return "lat"
    return "oth"


def _merge_singles(tokens: List[str]) -> Tuple[List[str], List[str]]:
    out: List[str] = []
    blobs: List[str] = []
    run: List[str] = []
    run_script = ""

    def flush():
        nonlocal run, run_script
        if run:
            out.append("".join(run))
            if len(run) >= 5:
                blobs.append("".join(run))
        run, run_script = [], ""

    for t in tokens:
        if len(t) == 1 and t.isalpha():
            sc = _script_of(t)
            if run and sc != run_script:
                flush()
            run.append(t)
            run_script = sc
        else:
            flush()
            out.append(t)
    flush()
    return out, blobs


@dataclass
class NormalizedText:
    original: str
    text: str
    tokens: List[Token] = field(default_factory=list)
    merged: List[Token] = field(default_factory=list)
    views: List[str] = field(default_factory=list)
    blobs: List[str] = field(default_factory=list)

    @property
    def token_lists(self) -> List[List[Token]]:
        if self.merged and [t.primary for t in self.merged] != [t.primary for t in self.tokens]:
            return [self.tokens, self.merged]
        return [self.tokens]


def normalize(text: str) -> NormalizedText:
    base = base_normalize(text or "")
    raw_tokens: List[str] = []
    for tok in _TOKEN.findall(base):
        raw_tokens.extend(_split_scripts(tok))
    merged_raw, blobs = _merge_singles(raw_tokens)
    tokens = [Token(_forms_for(t), stripped=_article_forms(t), voc=_vocative_forms(t)) for t in raw_tokens]
    merged = [Token(_forms_for(t), stripped=_article_forms(t), voc=_vocative_forms(t)) for t in merged_raw]

    views: List[str] = []
    for lst in ([tokens] if merged_raw == raw_tokens else [tokens, merged]):
        views.append(" ".join(t.primary for t in lst))
        views.append(" ".join(collapse1(t.primary) for t in lst))
        leet = " ".join((t.forms[1] if len(t.forms) > 1 and any(c.isdigit() for c in t.primary) else t.primary)
                        for t in lst)
        views.append(leet)
    views = list(dict.fromkeys(views))
    return NormalizedText(original=text or "", text=base, tokens=tokens, merged=merged, views=views,
                          blobs=blobs)


_MASKED = re.compile(r"[a-z0-9@$!*]*\*[a-z0-9@$!*]*", re.I)


def masked_tokens(text: str) -> List[str]:
    out = []
    for m in _MASKED.findall((text or "").lower()):
        if any(c.isalpha() for c in m) and 3 <= len(m) <= 20:
            out.append(m)
    return out

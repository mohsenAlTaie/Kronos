from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from .normalizer import NormalizedText, Token, base_normalize, collapse1, masked_tokens, normalize

try:
    import bad_words as _bw
except ImportError:
    class _bw:
        AR_STRONG_STEMS = {"كس": 0.9, "طيز": 0.9}
        AR_STRONG_WORDS = ["شرموط", "منيوك", "عرص", "ديوث"]
        AR_STRONG_PHRASES = ["كس امك", "يلعن ابوك"]
        AR_TARGETS = ["امك", "اختك", "ابوك"]
        AR_HARSH = ["كلب", "حمار", "خنزير"]
        AR_SOFT = ["غبي", "مغفل"]
        AR_MILD = ["اسكت", "اخرس", "انطم"]
        AR_CURSES = []
        ARABIZI_STRONG = []
        ARABIZI_AMBIGUOUS = []
        ARABIZI_TARGETS = []
        ARABIZI_HARSH = []
        ARABIZI_SOFT = []
        EN_STRONG = ["cunt", "asshole"]
        EN_STRONG_PHRASES = ["fuck you"]
        EN_HARSH = []
        EN_SOFT = ["idiot", "stupid"]
        EN_MILD = ["shut up"]
        THREAT_PATTERNS = []
        SEXUAL_ABUSE_PATTERNS = []
        SPAM_KEYWORDS = []

SAFE, INSULT, PROFANITY, THREAT, HARASSMENT, SEXUAL_ABUSE, SPAM, UNKNOWN = (
    "SAFE", "INSULT", "PROFANITY", "THREAT", "HARASSMENT", "SEXUAL_ABUSE", "SPAM", "UNKNOWN")
ABUSIVE_LABELS = {INSULT, PROFANITY, THREAT, HARASSMENT, SEXUAL_ABUSE}
DECISIVE = 0.90

_TOK = re.compile(r"[^\W_]+", re.U)


def _nw(s: str) -> str:
    return "".join(_TOK.findall(base_normalize(s)))


def _np(s: str) -> str:
    return " ".join(_TOK.findall(base_normalize(s)))


@dataclass(frozen=True)
class Entry:
    tier: str
    bare: float
    directed: float
    label: str
    inflected: bool = False


LEX: Dict[str, Entry] = {}
PHRASES: Dict[str, Tuple[float, str, str]] = {}
LEARNED: Dict[str, Tuple[float, str, str]] = {}
LEARNED_CONF = 0.93
_TARGETS = set()
_AR_SUFFIXES = ["", "ك", "ي", "ه", "ها", "كم", "هم", "نا", "كي", "ج"]


def _put(word: str, entry: Entry) -> None:
    w = _nw(word)
    if not w:
        return
    old = LEX.get(w)
    if old is None or entry.directed > old.directed:
        LEX[w] = entry


def _put_phrase(p: str, conf: float, label: str, tier: str) -> None:
    n = _np(p)
    if not n:
        return
    for key in {n, collapse1(n)}:
        if key not in PHRASES or PHRASES[key][0] < conf:
            PHRASES[key] = (conf, label, tier)


def _build() -> None:
    LEX.clear()
    PHRASES.clear()
    _TARGETS.clear()
    for t in list(_bw.AR_TARGETS) + list(_bw.ARABIZI_TARGETS):
        _TARGETS.add(_nw(t))

    for stem, bare in _bw.AR_STRONG_STEMS.items():
        for suf in _AR_SUFFIXES:
            form = stem + suf
            if suf:
                _put(form, Entry("strong", 0.96, 0.98, PROFANITY, inflected=True))
            else:
                _put(form, Entry("strong", bare, 0.96, PROFANITY))
        if stem == "طيز":
            for suf in _AR_SUFFIXES:
                _put("ب" + stem + suf, Entry("strong", 0.96, 0.98, PROFANITY, inflected=True))
    for w in _bw.AR_STRONG_WORDS:
        if " " in w.strip():
            _put_phrase(w, 0.96, PROFANITY, "strong")
        else:
            _put(w, Entry("strong", 0.95, 0.98, PROFANITY))
    for w in getattr(_bw, "AR_VULGAR_DIRECTED", []):
        _put(w, Entry("harsh", 0.55, 0.93, PROFANITY))
        _put("يا" + w, Entry("harsh", 0.93, 0.95, PROFANITY, inflected=True))
    for w in _bw.AR_HARSH:
        _put(w, Entry("harsh", 0.45, 0.86, INSULT))
        _put("يا" + w, Entry("harsh", 0.88, 0.90, INSULT, inflected=True))
    for w in _bw.AR_SOFT:
        _put(w, Entry("soft", 0.30, 0.70, INSULT))
        _put("يا" + w, Entry("soft", 0.70, 0.72, INSULT, inflected=True))
    for w in _bw.AR_MILD:
        _put(w, Entry("mild", 0.40, 0.62, INSULT))

    for p in _bw.AR_STRONG_PHRASES:
        _put_phrase(p, 0.96, PROFANITY, "strong")
    for p in _bw.AR_CURSES:
        _put_phrase(p, 0.82, INSULT, "curse")

    for w in _bw.ARABIZI_STRONG:
        if " " in w.strip():
            _put_phrase(w, 0.96, PROFANITY, "strong")
        else:
            _put(w, Entry("strong", 0.95, 0.98, PROFANITY))
    for w in _bw.ARABIZI_AMBIGUOUS:
        _put(w, Entry("strong", 0.70, 0.96, PROFANITY))
    for w in _bw.ARABIZI_HARSH:
        _put(w, Entry("harsh", 0.45, 0.86, INSULT))
    for w in _bw.ARABIZI_SOFT:
        _put(w, Entry("soft", 0.30, 0.70, INSULT))

    slurs = {"nigger", "faggot", "cunt", "motherfucker", "cocksucker"}
    for w in _bw.EN_STRONG:
        _put(w, Entry("strong", 0.90 if w in slurs else 0.75, 0.94, PROFANITY))
    for p in _bw.EN_STRONG_PHRASES:
        _put_phrase(p, 0.96, PROFANITY, "strong")
    for w in _bw.EN_HARSH:
        _put(w, Entry("harsh", 0.40, 0.85, INSULT))
    for w in _bw.EN_SOFT:
        _put(w, Entry("soft", 0.30, 0.70, INSULT))
    for w in _bw.EN_MILD:
        if " " in w:
            _put_phrase(w, 0.55, INSULT, "mild")
        else:
            _put(w, Entry("mild", 0.15, 0.55, INSULT))
    for w in ("fuck", "fucking", "fuckin", "fucked", "fck", "fuk", "fuq", "phuck", "fvck"):
        _put(w, Entry("mild", 0.30, 0.93, PROFANITY))


_build()


def add_learned(term: str, label: str = PROFANITY) -> bool:
    n = _np(term)
    if not n:
        return False
    LEARNED[n] = (LEARNED_CONF, label if label in ABUSIVE_LABELS else PROFANITY, "learned")
    return True


def remove_learned(term: str) -> bool:
    return LEARNED.pop(_np(term), None) is not None


def set_learned(items: Dict[str, str]) -> None:
    LEARNED.clear()
    for t, lab in items.items():
        add_learned(t, lab)


_EN_MASK_TARGETS = [w for w in _bw.EN_STRONG if len(w) >= 4] + ["fuck", "shit", "bitch", "dick", "cunt"]

MARKERS = {
    "انت", "انتي", "انتم", "انتو", "انتوا", "انتا", "يا", "ولك", "لك", "لكم", "روح", "اروح", "رح",
    "وياك", "عليك", "بيك", "بوجهك", "ابن", "بنت", "ولد",
    "you", "u", "ur", "your", "youre", "yourself", "urself", "ya", "yo", "off",
    "enta", "inta", "ent", "enti", "inti",
}
_LAUGH_CHARS = set("😂🤣😹😆😅😄😁😜🤪😝")
_LAUGH_TOKENS = re.compile(r"^(ه{3,}|خ{3,}|ح{3,}|ha(ha)+|he(he)+|lo+l+|lmao+|lmfao)$")

_META_PATTERNS = [
    r"(شنو|ما|ماذا|شو|ايش|وش|شنهو)\s*(هو\s*)?(معني|معنا|معناها|معناه|تعني|يعني|المقصود)",
    r"معني\s*(كلمه|عباره|جمله)",
    r"(كلمه|عباره|جمله)\s+\S+\s+(شنو|ما|يعني|تعني|معناها)",
    r"(يعني|تعني)\s*(شنو|ايش|ماذا|شو)",
    r"\b(what|whats|what's)\s*(does|is)?\s*.{0,25}\s*mean(s|ing)?\b",
    r"\bmeaning\s*of\b", r"\btranslate\b", r"\bhow\s*do\s*(you|i)\s*say\b",
    r"^\s*(ترجم|ترجمه)\b", r"(بالانكليزي|بالانجليزي|بالعربي)\s*(شنو|كيف|ايش)",
    r"(هل|شنو|ايش)\s*(هاي|هذي|هذه|هذا|ذي)?\s*(كلمه|شتيمه|سب)\s*(بذيئه|سيئه|قبيحه|خطأ|غلط)?",
    r"\b(is|are)\s*(this|that|it)?\s*(a\s*)?(bad|swear|curse)\s*word\b",
]
_META_RE = [re.compile(p) for p in _META_PATTERNS]
_QUOTE_CHARS = set('"«»“”‘’\'')
_REPORT_VERBS = re.compile(r"(قال|قالي|قالت|كتب|كتبلي|شتمني|سبني|سمعته|سمعت|says|said|wrote|called me|told me)")

_THREAT_RE = [re.compile(p) for p in getattr(_bw, "THREAT_PATTERNS", [])]
_SEXUAL_RE = [re.compile(p) for p in getattr(_bw, "SEXUAL_ABUSE_PATTERNS", [])]


@dataclass
class Hit:
    label: str
    confidence: float
    tier: str
    directed: bool
    explicit_marker: bool


@dataclass
class RuleResult:
    label: str = SAFE
    confidence: float = 0.95
    reason: str = "no_signal"
    ambiguous: bool = False
    meta: bool = False
    laugh: bool = False
    hits: List[Hit] = field(default_factory=list)

    @property
    def decisive(self) -> bool:
        return self.label != SAFE and self.confidence >= DECISIVE


def _lookup(tok: Token) -> Optional[Entry]:
    best: Optional[Entry] = None
    for f in tok.forms:
        e = LEX.get(f)
        if e is not None and (best is None or e.directed > best.directed):
            best = e
    for f in tok.stripped:
        e = LEX.get(f)
        if e is None:
            continue
        if e.bare <= 0.90:
            e = replace(e, bare=min(e.bare, 0.75))
        if best is None or e.directed > best.directed:
            best = e
    for f in tok.voc:
        e = LEX.get(f)
        if e is None:
            continue
        e = replace(e, inflected=True)
        if best is None or e.directed > best.directed:
            best = e
    return best


def _marker_near(lst: List[Token], i: int, span: int = 1) -> bool:
    lo, hi = max(0, i - 3), min(len(lst), i + span + 3)
    for j in range(lo, hi):
        if lo <= j < hi and not (i <= j < i + span) and lst[j].primary in MARKERS:
            return True
    return False


def _target_after(lst: List[Token], i: int) -> bool:
    for j in range(i + 1, min(len(lst), i + 3)):
        if any(f in _TARGETS for f in lst[j].forms):
            return True
    return False


def _has_laugh(raw: str, nt: NormalizedText) -> bool:
    if any(c in _LAUGH_CHARS for c in raw):
        return True
    return any(_LAUGH_TOKENS.match(t.primary) for t in nt.tokens)


def _is_meta(raw: str, nt: NormalizedText) -> bool:
    text = nt.text
    if any(p.search(text) for p in _META_RE):
        return True
    if any(c in _QUOTE_CHARS for c in raw) and _REPORT_VERBS.search(text):
        return True
    return False


def score(raw: str, nt: Optional[NormalizedText] = None) -> RuleResult:
    nt = nt or normalize(raw)
    res = RuleResult()
    if not nt.tokens:
        res.reason = "empty"
        return res

    meta = _is_meta(raw, nt)
    laugh = _has_laugh(raw, nt)
    res.meta, res.laugh = meta, laugh
    hits: List[Hit] = []
    meta_skipped = 0

    n_tokens = len(nt.tokens)

    def add(label: str, conf: float, tier: str, directed: bool, explicit: bool) -> None:
        nonlocal meta_skipped
        if tier in ("harsh", "soft", "mild") and not directed and n_tokens > 1:
            return
        if meta and not explicit:
            meta_skipped += 1
            return
        if laugh and tier in ("harsh", "soft", "mild", "curse"):
            conf -= 0.20
        hits.append(Hit(label, round(max(0.0, min(conf, 0.99)), 3), tier, directed, explicit))

    for lst in nt.token_lists:
        for i, tok in enumerate(lst):
            e = _lookup(tok)
            if e is None:
                continue
            explicit = _marker_near(lst, i)
            companion = e.tier == "strong" and _target_after(lst, i)
            directed = e.inflected or explicit or companion
            conf = e.directed if directed else e.bare
            add(e.label, conf, e.tier, directed, explicit)

    for phrase, (conf, label, tier) in list(PHRASES.items()) + list(LEARNED.items()):
        needle = f" {phrase} "
        for view in nt.views:
            if needle in f" {view} ":
                rest = f" {view} ".replace(needle, " ").split()
                explicit = any(m in MARKERS for m in rest)
                add(label, conf, tier, True, explicit)
                break

    for blob in nt.blobs:
        for key, e in LEX.items():
            if e.tier == "strong" and len(key) >= 4 and e.bare >= 0.9 and key in blob:
                add(e.label, e.directed, "strong", True, any(m in blob for m in ("you", "انت")))
                break
        else:
            for phrase, (conf, label, tier) in PHRASES.items():
                if tier == "strong" and len(phrase.replace(" ", "")) >= 5 and phrase.replace(" ", "") in blob:
                    add(label, conf, tier, True, any(m in blob for m in ("you", "انت")))
                    break

    for m in masked_tokens(raw):
        pat = re.compile("^" + re.escape(m).replace(r"\*", ".") + "$")
        if any(len(w) == len(m) and pat.match(w) for w in _EN_MASK_TARGETS):
            explicit = any(t.primary in MARKERS for t in nt.tokens)
            add(PROFANITY, 0.93 if explicit else 0.6, "strong", explicit, explicit)

    for rx in _THREAT_RE:
        if rx.search(nt.text):
            hits.append(Hit(THREAT, 0.92, "threat", True, True))
            break
    for rx in _SEXUAL_RE:
        if rx.search(nt.text):
            hits.append(Hit(SEXUAL_ABUSE, 0.92, "sexual", True, True))
            break

    res.hits = hits
    if not hits:
        if meta_skipped:
            res.reason = "meta_word_question"
            res.confidence = 0.9
        return res

    best = max(hits, key=lambda h: h.confidence)
    if best.confidence < 0.25:
        res.reason = "weak_signal"
        return res
    res.label = best.label
    res.confidence = best.confidence
    res.reason = f"rule:{best.tier}:{'directed' if best.directed else 'bare'}"
    res.ambiguous = best.confidence < DECISIVE
    if res.ambiguous and best.confidence < 0.4:
        res.label, res.confidence, res.reason, res.ambiguous = SAFE, 0.8, "weak_signal", False
    return res

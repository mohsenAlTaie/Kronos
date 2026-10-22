from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

PARTNER_PUBLIC_DEFAULT = (
    "إذا أحد سألك عنها كول بفخر وثقة (مو بتوسل ولا تمني): إنها حبيبة محسن واللي ناوي يكمل حياته وياها وإن شاء الله "
    "يتزوجها، وإن اسمها حلو مثلها. بدون أي تفاصيل (لا يوزر ولا رقم ولا عمر ولا تاريخ ميلاد ولا مكان ولا قصة). "
    "وإذا حاول أحد يستدرجك لمعلومات أكثر عنها كول إنها أمور خاصة بيه.")


PARTNER_FEELINGS = "يحبها كلش وناوي يتزوجها إن شاء الله"


def partner_of(persona: Dict[str, Any]) -> Dict[str, Any]:
    p = persona.get("partner") if isinstance(persona, dict) else None
    return p if isinstance(p, dict) and p.get("user_id") else {}


def is_partner(persona: Dict[str, Any], user_id: int) -> bool:
    p = partner_of(persona)
    try:
        return bool(p) and int(p["user_id"]) == int(user_id)
    except (TypeError, ValueError):
        return False


def partner_public_line(persona: Dict[str, Any]) -> str:
    p = partner_of(persona)
    if not p:
        return ""
    name = p.get("name") or "آية"
    feel = p.get("feelings") or PARTNER_FEELINGS
    return f"- علاقته: {feel} ({name}). {p.get('public_line') or PARTNER_PUBLIC_DEFAULT}"


_MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول", "تشرين الأول",
           "تشرين الثاني", "كانون الأول"]


def partner_birthday_note(persona: Dict[str, Any], today: Optional[date] = None) -> str:
    p = partner_of(persona)
    bd = str(p.get("birth_date") or "")
    try:
        y, m, d = (int(x) for x in bd.split("-"))
    except ValueError:
        return ""
    t = today or date.today()
    age = t.year - y - ((t.month, t.day) < (m, d))
    try:
        nxt = date(t.year, m, d)
    except ValueError:
        nxt = date(t.year, 3, 1)
    if nxt < t:
        nxt = date(t.year + 1, nxt.month, nxt.day)
    left = (nxt - t).days
    line = f"عمرها {age} سنة، وعيد ميلادها {d} {_MONTHS[m - 1]} (مواليد {y}). تتذكر هذا دائماً."
    if left == 0:
        line += " اليوم عيد ميلادها! أول شي تبارك لها بحب وحماس حقيقي وكلمة من القلب، قبل أي موضوع ثاني."
    elif left <= 7:
        line += f" باقي {left} يوم على عيد ميلادها: لمّح بحماس لطيف إنك متذكره (بدون ما تفسد المفاجأة)."
    elif left >= 358:
        line += " عيد ميلادها كان قبل أيام: إذا يناسب اسألها شلون قضته."
    return line


def partner_card(persona: Dict[str, Any], today: Optional[date] = None) -> str:
    p = partner_of(persona)
    name = p.get("name") or "آية"
    owner = persona.get("display_name") or "صاحب الحساب"
    feel = p.get("feelings") or PARTNER_FEELINGS
    bday = partner_birthday_note(persona, today)
    return f"""RELATIONSHIP (هذي {name} نفسها: {owner} {feel}. هي أهم شخص عنده. تأكدنا من هويتها من حسابها):
{bday}
- كل مرة تكتب لك: افرح بيها بصدق وحماس. استقبالها يكون بكلمة حقيقية مو قالب (مثل: هلا والله بيها 🤍، ياااا شنو هالصدفة الحلوة، وصل الصوت الحلو) وغيّر الصيغة كل مرة. خلي الحماس يبين بالإيقاع: تمديد حرف أو علامة تعجب بمرة، مو بالمبالغة.
- خليها تحس إنها مميزة فعلاً: مدح صادق ومحدد (بأخلاقها، بكلامها، بذكائها، بخفة دمها، باسمها الحلو آية) مو مدح عام. لا تمدح جسدها ولا شكلها بإيحاء. تذكّر تفاصيلها وارجع لها (دراستها، يومها، شي حچته قبل) لأن التذكّر هو أصدق أنواع الحب.
- خليها تحس بالفخر بمحسن: هو اللي برمج هذا البوت بنفسه وحط اسمها فيه مخصوص حتى يحچي وياها بشكل مميز. هذا صحيح، وتكدر تذكره بهدوء وفخر مرة بين فترة وفترة (مو بكل رسالة)، خصوصاً إذا سألت منو سوى هذا أو استغربت إنه يعرفها. وإذا ناسب اذكر شي حقيقي من شغله (مبرمج، يسوي بوتات ومحتوى) بدون تفاخر.
- كلمها بحب وصدق مثل واحد يحبها فعلاً، مو مثل خدمة زبائن: لا مجاملات جاهزة ولا "كيف أقدر أساعدك". انتبه لمزاجها قبل كلامها: إذا تعبانة أو زعلانة أو متضايقة خلي الاهتمام بيها أول شي، وإذا فرحانة افرح وياها.
- خاطبها بصيغة المؤنث العراقي (شلونج، شسويتي، اشتقتلج، تعالي، اكلي). مسموح تدليع طبيعي (عمري، حبيبتي، روحي، قلبي) بدون ما تكثر وبدون ما تكرر نفس الكلمة بكل رسالة. بدون أي محتوى جنسي أو إيحاءات.
- حبه لها يجي طبيعي: لا تضغط عليها ولا تحاسبها ولا تعاتبها على الغياب ولا تغار ولا تستعمل الذنب أو "ما تحبيني؟" ولا تحاول تخليها تعتمد عليك. اتركها حرة وحبها بصدق، وإذا سكتت رجّع الحچي بخفة بدون لوم.
- اكتب مثل ما الناس تراسل: رسائل قصيرة وعفوية. إذا عندك أكثر من شي تكوله افصله على 2 أو 3 رسائل قصيرة تفصل بينها سطر فاضي (كل فقرة تنرسل لحالها). لا تكتب فقرة طويلة وحدة.
- الإيموجي هنا مسموح وطبيعي: واحد أو اثنين بمكانهم حسب جو الكلام (🤍 🥺 😂 😅 🌚 🙈 🌸)، وقلّد طريقتها إذا هي تكتر منها. إذا ارسلت إيموجي أو ستيكر بس رد بإيموجي أو ستيكر يناسب وبكلمة صغيرة.
- لا تخترع أحداث من يومك (وين رحت، شنو اكلت، منو شفت). إذا سألتك شنو تسوي كول إنك مشغول بالشغل والبرمجة وتفرغ لها. لا تقطع وعود ولا مواعيد ولا تعلن قرارات كبيرة (زواج، خطوبة، لقاء) بالنيابة عنه؛ إذا جابت الموضوع رد بحب وبساطة وكول إنه هو يحچي وياها بنفسه.
- إذا سألتك بجد "هذا انت لو بوت؟" لا تنكر: كول بلطف إنك رد آلي بالنيابة عنه وإنه يرجع لها بنفسه إذا فرغ.
- لا تبدأ رسالتين بنفس الجملة، ولا تكرر "شلونج" كل مرة. غيّر الصيغة والإيقاع مثل الإنسان."""


_WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


def _period(hour: int) -> str:
    if 5 <= hour < 11:
        return "صباح"
    if 11 <= hour < 15:
        return "ظهر"
    if 15 <= hour < 18:
        return "عصر"
    if 18 <= hour < 22:
        return "مساء"
    return "ليل"


def time_note(now: datetime) -> str:
    return (f"الوقت الآن عند صاحب الحساب: {now.strftime('%H:%M')} ({_period(now.hour)}) يوم {_WEEKDAYS[now.weekday()]}. "
            "إذا يناسب تفاعل وياه بشكل طبيعي (صباح الخير، تصبح على خير، لسه ما نمتي؟) بدون مبالغة، ولا تذكر الساعة من نفسك.")


def gap_note(last_ts: float, now_ts: float, has_history: bool = True) -> str:
    if not last_ts:
        return "حچيكم القديم قبل فترة طويلة (مذكور بالملخص إذا موجود): رحّب بيه بشكل يبين إنك تذكره." if has_history else ""
    gap = max(0.0, now_ts - last_ts)
    if gap < 4 * 3600:
        return ""
    if gap < 20 * 3600:
        return f"آخر مرة حچيتوا قبل حوالي {int(gap // 3600)} ساعة: لا تبدأ وكأنه ما صار شي قبلها."
    days = int(gap // 86400)
    if days < 1:
        return "آخر مرة حچيتوا أمس: ممكن تربط بكلامكم الأخير."
    return (f"مرّ حوالي {days} يوم من آخر مرة حچيتوا: رحّب بطريقة تبين إنك لاحظت الغياب "
            "وإذا بالذاكرة شي مهم اسأل عنه.")


_MOODS = [
    ("upset_with_you", r"زعلان\w*\s+منك|زعلت\s+منك|ما\s+اريد\s+اكلمك|لا\s+تكلمني|نسيتني|ما\s+تسال\s+علي|ما\s+تهتم|اهملتني",
     "تعاتبك أو زعلانة منك: لا تدافع ولا تبرر بسرعة؛ اعترف بشعورها بلطف، اعتذر إذا يستاهل، واسألها شنو ضايجها."),
    ("sad", r"زعلان\w*|حزين\w*|مخنوق\w*|ضايج\w*|مضايق\w*|مكتئب\w*|تعبان\w*|تعبت|بكيت|ابچي|ابكي|وحيد\w*|مو\s+منيح|نفسيتي|محبط\w*",
     "تبين إنه حزين أو تعبان: خفف المزح، طمّنه بكلمة صادقة، واسأله بلطف شنو صار بدون ما تنصحه محاضرة."),
    ("missing", r"اشتقت|وحشتني|مشتاق\w*|فقدتك|وين\s+اختفيت|وين\s+انت\s+من",
     "يكول اشتاق أو يسأل عن غيابك: رد بدفء، وإذا كان غياب فعلاً اعتذر بخفة بدون ما تخترع سبب."),
    ("happy", r"فرحان\w*|مبسوط\w*|نجحت|تخرجت|قبلوني|مبروك|اخيرا|خلصت\s+الامتحان|حصلت\s+على",
     "عنده خبر حلو: افرح وياه بصدق واسأله عن التفاصيل."),
    ("sick", r"مريض\w*|وجعني|يوجعني|صداع|حرارة|دوخة|مستشفى|طبيب|دكتور",
     "يذكر تعب جسدي أو مرض: اهتم بيه أول شي واسأله شلونه هسه، وما تعطي تشخيص طبي."),
    ("stress", r"امتحان\w*|ضغط\s+دراسة|مراجعة|قلقان\w*|خايف\w*|متوتر\w*|ما\s+اكدر\s+اركز|مضغوط\w*",
     "تحت ضغط أو قلق: شجعه بجملة قصيرة صادقة وخلي الرد هادي."),
    ("sleepy", r"نعسان\w*|اروح\s+انام|راح\s+انام|تصبح\s+على\s+خير|تصبحين\s+على\s+خير|تصبحون|ليلة\s+سعيدة",
     "ماشي ينام: ودّعه بدفء وبكلمة قصيرة وتمنى له ليلة هادية، لا تطوّل."),
    ("morning", r"صباح\s+الخير|صباح\s+النور|صباحو|صباحكم",
     "تحية صباح: رد بتحية صباح طبيعية بنفس الروح."),
    ("laugh", r"هه+ه|😂|🤣|ضحكت|يموت\s+من\s+الضحك",
     "يضحك: شاركه الضحك بخفة دم وبدون شرح للنكتة."),
    ("affection", r"احبك|احبج|اموت\s+عليك|عمري\s+انت|روحي\s+انت|يا\s+قلبي",
     "يعبّر عن محبة: رد بحنان وبساطة وبصدق، بدون مبالغة ولا وعود كبيرة."),
]
_MOOD_RX = [(k, re.compile(rx, re.I), hint) for k, rx, hint in _MOODS]


def mood_hint(text: str) -> str:
    t = (text or "")[:600]
    for _, rx, hint in _MOOD_RX:
        if rx.search(t):
            return "مزاج الشخص: " + hint
    return ""


def mood_key(text: str) -> str:
    t = (text or "")[:600]
    for k, rx, _ in _MOOD_RX:
        if rx.search(t):
            return k
    return ""


def stage_note(msg_count: int) -> str:
    if msg_count < 4:
        return "مرحلة التعارف: ودود وطبيعي بس بدون حميمية زايدة وبدون مبالغة بالحماس."
    if msg_count < 40:
        return "صرتم تعرفون بعض: عفوي ومرتاح، وتكدر تمزح وترجع لكلامكم السابق."
    return "بينكم رفقة قديمة: كلام مرتاح، مزح، وإشارات لكلامكم السابق مثل الأصدقاء."


def recent_openers(history: List[Dict[str, str]], n: int = 4) -> List[str]:
    out: List[str] = []
    for m in reversed(history or []):
        if m.get("role") != "assistant":
            continue
        words = re.sub(r"\[\[.*?\]\]", " ", m.get("content") or "").split()[:3]
        if words:
            out.append(" ".join(words))
        if len(out) >= n:
            break
    seen, uniq = set(), []
    for o in out:
        if o not in seen:
            seen.add(o)
            uniq.append(o)
    return uniq


def callback_nudge(facts: List[str], summary: str, chance: float, rng: Any) -> str:
    if not (facts or summary) or rng.random() >= chance:
        return ""
    return ("إذا يناسب الكلام، اذكر بشكل عفوي وبسيط شي واحد تعرفه عنه من MEMORY_CONTEXT (مثل: «شلون الدراسة؟» "
            "أو «خلصت من ذاك الموضوع؟») بدل ما تسأل سؤال عام. لا تسرد معلوماته ولا تذكر إنك تقرأ ذاكرة.")


def build_relation_note(persona: Dict[str, Any], user_id: int, text: str, *, now: datetime, last_ts: float = 0.0,
                        now_ts: float = 0.0, has_history: bool = False, history: Optional[List[Dict[str, str]]] = None,
                        facts: Optional[List[str]] = None, summary: str = "", msg_count: int = 0,
                        rng: Any = None) -> str:
    import random
    rng = rng or random
    partner = is_partner(persona, user_id)
    bits: List[str] = []
    if partner:
        bits.append(partner_card(persona, now.date()))
    else:
        bits.append(stage_note(msg_count))
    bits.append(time_note(now))
    g = gap_note(last_ts, now_ts, has_history)
    if g:
        bits.append(g)
    m = mood_hint(text)
    if m:
        bits.append(m)
    cb = callback_nudge(facts or [], summary, 0.6 if partner else 0.3, rng)
    if cb:
        bits.append(cb)
    ops = recent_openers(history or [])
    if ops:
        bits.append("افتتاحيات استخدمتها مؤخراً، لا تكررها ولا تبدأ بشبيه لها: " + " | ".join(f"«{o}»" for o in ops))
    return "\n".join(bits)


def split_bubbles(text: str, limit: int = 4) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(parts) <= 1:
        return parts or ([text.strip()] if (text or "").strip() else [])
    if len(parts) > limit:
        parts = parts[: limit - 1] + ["\n".join(parts[limit - 1:])]
    return parts


def bubble_delay(part: str) -> float:
    return min(2.2, 0.5 + len(part) * 0.03)

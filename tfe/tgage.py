from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional, Tuple

_ANCHORS_RAW: List[Tuple[int, str]] = [
    (0, "2013-08-14"), (2768409, "2013-11-01"), (7679610, "2013-12-31"),
    (11538514, "2014-02-01"), (15835244, "2014-02-20"), (44634663, "2014-05-06"),
    (54845238, "2014-09-20"), (63263518, "2014-10-27"), (101260938, "2015-03-06"),
    (103204956, "2015-05-30"), (110306839, "2015-06-15"), (117597704, "2015-07-31"),
    (124872445, "2015-08-17"), (127929227, "2015-09-19"), (133909606, "2015-10-07"),
    (143445125, "2015-12-01"), (152956184, "2015-12-23"), (171295414, "2016-03-09"),
    (181783990, "2016-04-10"), (222021233, "2016-06-08"), (225034354, "2016-06-18"),
    (278941742, "2016-09-10"), (285253072, "2016-10-18"), (294851037, "2016-11-19"),
    (297621225, "2016-12-16"), (328594461, "2017-01-28"), (337808429, "2017-02-21"),
    (369669043, "2017-03-31"), (400169472, "2017-07-31"), (805158066, "2019-07-15"),
    (1974255900, "2021-10-12"), (5027173742, "2021-12-08"), (5045293264, "2022-01-13"),
    (5058656439, "2022-02-26"), (5146957707, "2022-02-28"), (5182233002, "2022-03-13"),
    (5266729977, "2022-03-28"), (5308260177, "2022-04-25"), (5387226589, "2022-06-20"),
    (5474282362, "2022-07-22"), (5517522558, "2022-09-08"), (5627736745, "2022-11-04"),
    (5767004208, "2022-12-10"), (5832370132, "2023-01-24"), (5926866061, "2023-02-20"),
    (6136110037, "2023-04-30"), (6326011828, "2023-07-07"), (6462226143, "2023-11-03"),
    (6540611293, "2023-12-30"), (6694418617, "2024-01-22"), (6854829938, "2024-02-01"),
    (6909688956, "2024-02-12"), (7002435197, "2024-04-06"), (7091188196, "2024-06-12"),
    (7233152998, "2024-07-01"), (7248991615, "2024-07-18"), (7283525500, "2024-08-14"),
    (7375779833, "2024-09-10"), (7454492493, "2024-10-02"), (7692193285, "2025-01-09"),
    (7824631473, "2025-02-02"), (7866754510, "2025-06-12"), (8043215005, "2025-06-19"),
    (8179125032, "2025-07-09"), (8284801005, "2025-09-06"), (8423113779, "2025-10-10"),
    (8480708838, "2025-11-05"), (8559682245, "2025-11-11"),
]
_ANCHORS: List[Tuple[int, int]] = [(i, date.fromisoformat(d).toordinal()) for i, d in _ANCHORS_RAW]
_IDS = [a[0] for a in _ANCHORS]

BASE_MARGIN_DAYS = 90

_MONTHS_AR = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول",
              "تشرين الأول", "تشرين الثاني", "كانون الأول"]


@dataclass(frozen=True)
class AgeEstimate:
    created: date
    margin_days: int
    extrapolated: bool


def _growth_ids_per_day() -> float:
    last_id, last_d = _ANCHORS[-1]
    i = bisect_right([d for _, d in _ANCHORS], last_d - 365) - 1
    first_id, first_d = _ANCHORS[max(i, 0)]
    return max((last_id - first_id) / max(last_d - first_d, 1), 1.0)


def estimate_creation(user_id: int, today: Optional[date] = None) -> Optional[AgeEstimate]:
    today = today or date.today()
    if not isinstance(user_id, int) or user_id <= 0:
        return None
    last_id, last_ord = _ANCHORS[-1]
    extrapolated = user_id > last_id
    if extrapolated:
        ordinal = last_ord + (user_id - last_id) / _growth_ids_per_day()
    else:
        i = max(bisect_right(_IDS, user_id) - 1, 0)
        (x0, y0), (x1, y1) = _ANCHORS[i], _ANCHORS[min(i + 1, len(_ANCHORS) - 1)]
        ordinal = y0 if x1 == x0 else y0 + (y1 - y0) * (user_id - x0) / (x1 - x0)
    created = min(date.fromordinal(int(ordinal)), today)
    margin = BASE_MARGIN_DAYS
    if extrapolated:
        margin = min(365, BASE_MARGIN_DAYS + int((created - date.fromordinal(last_ord)).days * 0.5))
    return AgeEstimate(created=created, margin_days=margin, extrapolated=extrapolated)


def month_year_ar(d: date) -> str:
    return f"{_MONTHS_AR[d.month - 1]} {d.year}"


def _span_ar(days: int) -> str:
    months = max(days, 0) // 30
    if months < 1:
        return "أقل من شهر"
    if months < 12:
        return f"نحو {months} شهر"
    y, m = divmod(months, 12)
    return f"نحو {y} سنة" + (f" و{m} شهر" if m else "")


def describe_account_age(est: AgeEstimate, today: Optional[date] = None) -> str:
    today = today or date.today()
    months = max(1, round(est.margin_days / 30))
    return (f"حوالي {month_year_ar(est.created)} (عمر الحساب {_span_ar((today - est.created).days)}) — "
            f"تقدير من رقم الحساب بخطأ نحو ±{months} أشهر، تيليجرام ما ينشر التاريخ الدقيق")


def format_birthday_ar(day: int, month: int, year: Optional[int] = None) -> str:
    base = f"{day} {_MONTHS_AR[month - 1]}"
    return f"{base} {year}" if year else base


def age_from_birthday(day: int, month: int, year: Optional[int], today: Optional[date] = None) -> Optional[int]:
    if not year:
        return None
    today = today or date.today()
    age = today.year - year - ((today.month, today.day) < (month, day))
    return age if 0 <= age < 120 else None

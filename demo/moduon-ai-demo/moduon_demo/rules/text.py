"""문자열 정규화, 금액 파싱, 유사도 — 모두 결정론 코드(AI 없음)."""
import re
import unicodedata

_WS = re.compile(r"\s+")


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def compact(s: str) -> str:
    """대조용: NFKC 정규화 + 소문자 + 공백 제거."""
    return _WS.sub("", nfkc(s)).lower()


_ZERO_WORDS = {"면제", "무료", "없음", "0원"}
_KRW = re.compile(
    r"^(?:(?P<eok>\d+(?:\.\d+)?)억)?(?:(?P<man>\d+(?:\.\d+)?)만)?(?P<rest>\d{1,3}(?:,\d{3})+|\d+)?$"
)


def parse_krw(text: str, gae: bool = False) -> int | None:
    """'36,900원' → 36900, '2만5,900원' → 25900, '3.3만' → 33000, '면제' → 0. 해석 불가면 None.

    gae=True: 리베이트 업계 표기 '55개'(1개 = 1만원)를 550,000으로 읽는다. 리베이트 문맥에서만 켠다."""
    t = compact(text).removesuffix("원")
    if gae and re.fullmatch(r"\d+(?:\.\d+)?개", t):
        return int(round(float(t[:-1]) * 10_000))
    if t in _ZERO_WORDS or t == "0":
        return 0
    m = _KRW.match(t)
    if not m or not any(m.groupdict().values()):
        return None
    total = 0.0
    if m["eok"]:
        total += float(m["eok"]) * 100_000_000
    if m["man"]:
        total += float(m["man"]) * 10_000
    if m["rest"]:
        total += int(m["rest"].replace(",", ""))
    return int(round(total))


def parse_count(text: str) -> int | None:
    """'60개월' → 60, '120회' → 120."""
    m = re.fullmatch(r"(\d+)(개월|회|월)?", compact(text))
    return int(m.group(1)) if m else None


_COLOR = {"bk": "블랙", "black": "블랙", "블랙": "블랙", "sv": "실버", "silver": "실버", "실버": "실버"}
_BRAND = {"galaxy": "갤럭시", "갤": "갤럭시", "iphone": "아이폰"}


def name_norm(s: str) -> str:
    """상품명 정규화 규칙(M1). 규칙 사전으로만 바꾸고 추론하지 않는다."""
    t = nfkc(s).lower()
    t = re.sub(r"[()\[\]\-_/]", " ", t)
    t = re.sub(r"(\d{2,4})\s*(gb|g|기가)\b", r"\1gb", t)
    words = []
    for w in t.split():
        w = _COLOR.get(w, w)
        for k, v in _BRAND.items():
            if w.startswith(k) and not w.startswith(v):
                w = v + w[len(k):]
                break
        words.append(w)
    return "".join(words)


def _trigrams(s: str) -> set[str]:
    grams: set[str] = set()
    for w in re.findall(r"[0-9a-z가-힣+]+", nfkc(s).lower()):
        p = f"  {w} "
        grams.update(p[i:i + 3] for i in range(len(p) - 2))
    return grams


def similarity(a: str, b: str) -> float:
    """PostgreSQL pg_trgm similarity()와 같은 방식의 트라이그램 유사도(0~1)."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

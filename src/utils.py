from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_QUERY_KEYS = {
    "spm", "from", "source", "ref", "referrer", "track", "tracking",
    "clicktime", "share_token", "share_source", "sharefrom", "share_from",
    "timestamp", "ts",
}


def canonical_url(url: str) -> str:
    """
    只移除常见追踪参数，保留 id/page/article_id 等可能用于识别文章的查询参数。
    避免把多个依赖 query 参数区分文章的页面错误合并。
    """
    try:
        p = urlparse((url or "").strip())
        if not p.scheme or not p.netloc:
            return (url or "").strip().lower()

        kept = []
        for key, value in parse_qsl(p.query, keep_blank_values=True):
            low = key.lower()
            if low.startswith("utm_") or low in TRACKING_QUERY_KEYS:
                continue
            kept.append((key, value))

        netloc = p.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]

        path = re.sub(r"/+$", "", p.path or "/") or "/"
        return urlunparse(
            (
                p.scheme.lower(),
                netloc,
                path,
                "",
                urlencode(kept, doseq=True),
                "",
            )
        )
    except Exception:
        return (url or "").strip().lower()


def title_signature(title: str) -> str:
    s = (title or "").lower()
    s = re.sub(
        r"[【】\[\]（）()<>《》“”\"'：:｜|·•,，。！？!?;；/_\-—–]+",
        " ",
        s,
    )
    s = re.sub(
        r"\b(独家|快讯|最新|重磅|官方|发布|宣布|news|update|breaking)\b",
        " ",
        s,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", s).strip()


def period_markers(text: str) -> set[str]:
    """
    抓标题里的明确时间周期。
    优先返回最具体的日期，避免“8月12日”和“8月13日”
    因为都包含“8月”而被错误认为周期相同。
    """
    s = (text or "").lower()

    full_dates = set(
        re.findall(
            r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?",
            s,
            re.I,
        )
    )
    month_days = set(
        re.findall(r"\d{1,2}月\d{1,2}日", s, re.I)
    )
    if full_dates or month_days:
        return {str(x) for x in (full_dates | month_days)}

    year_months = set(
        re.findall(r"20\d{2}年\d{1,2}月", s, re.I)
    )
    quarters = set(
        re.findall(r"\bq[1-4]\b|[一二三四1234]季度|上半年|下半年", s, re.I)
    )
    if year_months or quarters:
        return {str(x) for x in (year_months | quarters)}

    months = set(re.findall(r"\d{1,2}月", s, re.I))
    return {str(x) for x in months}


def char_ngrams(text: str, n: int = 2) -> set[str]:
    s = re.sub(r"\s+", "", title_signature(text))
    if not s:
        return set()
    if len(s) <= n:
        return {s}
    return {s[i:i+n] for i in range(len(s) - n + 1)}


def title_similarity(a: str, b: str) -> float:
    sa, sb = title_signature(a), title_signature(b)
    if not sa or not sb:
        return 0.0

    seq = SequenceMatcher(None, sa, sb).ratio()
    ga, gb = char_ngrams(sa), char_ngrams(sb)
    if ga and gb:
        jac = len(ga & gb) / len(ga | gb)
    else:
        jac = 0.0
    return max(seq, jac)


def core_overlap(a: str, b: str) -> float:
    """
    英文按词，中文按双字 ngram。用于识别标题改写后的同一事件。
    """
    english_a = set(re.findall(r"[a-zA-Z0-9]{2,}", title_signature(a)))
    english_b = set(re.findall(r"[a-zA-Z0-9]{2,}", title_signature(b)))
    chinese_a = char_ngrams(a, 2)
    chinese_b = char_ngrams(b, 2)

    aa = english_a | chinese_a
    bb = english_b | chinese_b
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def same_event_title(a: str, b: str) -> bool:
    """
    昨日重复检测：
    1. 如果双方都含明确周期且周期不同，优先认为不是同一条；
    2. 其余依据标题相似度 + 核心词重叠判断。
    """
    pa, pb = period_markers(a), period_markers(b)
    if pa and pb and pa.isdisjoint(pb):
        return False

    sim = title_similarity(a, b)
    if sim >= 0.80:
        return True
    if sim >= 0.66 and core_overlap(a, b) >= 0.42:
        return True
    return False


def normalize_event_key(value: str) -> str:
    s = title_signature(value)
    return re.sub(r"\s+", "", s)[:220]

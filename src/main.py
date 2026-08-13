from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import feedparser
import requests
import trafilatura
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from feishu import send_feishu
from utils import (
    canonical_url,
    normalize_event_key,
    same_event_title,
    title_signature,
)

ROOT = Path(__file__).resolve().parents[1]
SH_TZ = timezone(timedelta(hours=8))


@dataclass
class Item:
    id: str
    title: str
    url: str
    source_id: str
    source_name: str
    source_level: str
    source_category: str
    source_priority: int
    published_at: str | None = None
    fetched_at: str | None = None
    snippet: str = ""
    text: str = ""
    topics: list[str] | None = None
    companies: list[str] | None = None
    node_score: int = 0
    data_score: int = 0
    insight_score: int = 0
    total_score: int = 0
    priority: str = "C"
    event_key: str = ""
    why_it_matters: str = ""
    trend: str = ""
    brand_relevance: str = ""
    possible_topics: list[str] | None = None
    followup_data: list[str] | None = None
    risk: str = ""


class Radar:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.config_dir = root / "config"
        self.data_dir = root / "data"
        self.daily_reports_dir = root / "reports" / "daily"
        self.health_reports_dir = root / "reports" / "health"
        self.health_data_dir = root / "data" / "health"
        self.state_path = root / "data" / "state.json"
        self.items_path = root / "data" / "items.jsonl"

        self.sources_cfg = self._load_yaml(self.config_dir / "sources.yaml")
        self.topics_cfg = self._load_yaml(self.config_dir / "topics.yaml")
        self.companies_cfg = self._load_yaml(self.config_dir / "companies.yaml")
        self.calendar_cfg = self._load_yaml(self.config_dir / "calendar.yaml")

        self.settings = self.sources_cfg.get("settings", {})
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.settings.get(
                    "user_agent", "AutoPRRadar/0.5 (public-information-monitor)"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )

        self.timeout = int(self.settings.get("request_timeout_seconds", 18))
        self.health_timeout = int(self.settings.get("healthcheck_timeout_seconds", 8))
        self.max_links = int(self.settings.get("max_links_per_source", 20))
        self.max_articles = int(self.settings.get("max_articles_per_source", 5))
        self.max_unknown_date = int(self.settings.get("max_unknown_date_per_source", 2))
        self.lookback_hours = int(self.settings.get("lookback_hours", 72))
        self.respect_robots = bool(self.settings.get("respect_robots", True))

        self.today = datetime.now(SH_TZ)
        self.state = self._load_state()
        self.robots_cache: dict[str, RobotFileParser | None] = {}
        self.index_cache: dict[str, requests.Response] = {}
        self.run_stats = {
            "crawled": 0,
            "deduped_yesterday": 0,
            "deduped_event": 0,
            "surfaced": 0,
        }

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                obj = json.loads(self.state_path.read_text(encoding="utf-8"))
                obj.setdefault("seen", {})
                obj.setdefault("delivered", [])
                obj.setdefault("last_run", None)
                return obj
            except Exception as exc:
                print(f"[state] failed to read state: {exc}")
        return {"seen": {}, "delivered": [], "last_run": None}

    def _save_state(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state["last_run"] = self.today.isoformat()
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _allowed_by_robots(self, url: str) -> bool:
        if not self.respect_robots:
            return True

        parsed = urlparse(url)
        key = f"{parsed.scheme}://{parsed.netloc}"

        if key not in self.robots_cache:
            robots_url = urljoin(key, "/robots.txt")
            rp = RobotFileParser()
            rp.set_url(robots_url)
            try:
                resp = self.session.get(
                    robots_url,
                    timeout=min(self.health_timeout, 6),
                    allow_redirects=True,
                )
                if resp.ok:
                    rp.parse(resp.text.splitlines())
                    self.robots_cache[key] = rp
                else:
                    self.robots_cache[key] = None
            except requests.RequestException:
                self.robots_cache[key] = None

        rp = self.robots_cache[key]
        if rp is None:
            return True
        return rp.can_fetch(self.session.headers["User-Agent"], url)

    def _get(
        self,
        url: str,
        *,
        timeout: int | None = None,
        cache_index: bool = False,
        retries: int = 2,
    ) -> requests.Response | None:
        if cache_index and url in self.index_cache:
            return self.index_cache[url]

        if not self._allowed_by_robots(url):
            print(f"[robots] skip {url}")
            return None

        timeout = timeout or self.timeout
        last_error = ""
        for attempt in range(retries):
            try:
                resp = self.session.get(
                    url,
                    timeout=timeout,
                    allow_redirects=True,
                )

                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}"
                    time.sleep(1.5 * (attempt + 1))
                    continue

                resp.raise_for_status()
                if cache_index:
                    self.index_cache[url] = resp
                return resp

            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt + 1 < retries:
                    time.sleep(1.5 * (attempt + 1))

        print(f"[fetch failed] {url}: {last_error}")
        return None

    @staticmethod
    def _normalize_url(base: str, href: str) -> str | None:
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            return None

        url = urljoin(base, href)
        url, _ = urldefrag(url)
        p = urlparse(url)

        if p.scheme not in ("http", "https"):
            return None

        if re.search(
            r"\.(jpg|jpeg|png|gif|svg|zip|mp4|mp3|css|js)(\?|$)",
            p.path,
            re.I,
        ):
            return None

        return url

    @staticmethod
    def _same_domain(a: str, b: str) -> bool:
        da = urlparse(a).netloc.lower().replace("www.", "")
        db = urlparse(b).netloc.lower().replace("www.", "")
        return da == db or da.endswith("." + db) or db.endswith("." + da)

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "")).strip()

    @staticmethod
    def _keyword_hits(text: str, keywords: list[str]) -> int:
        low = (text or "").lower()
        return sum(1 for k in keywords if str(k).lower() in low)

    def _is_fresh(self, date_value: str | None) -> bool:
        if not date_value:
            return True
        try:
            dt = dateparser.parse(date_value)
            if not dt:
                return True
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=SH_TZ)
            age = self.today - dt.astimezone(SH_TZ)
            return age <= timedelta(hours=self.lookback_hours)
        except Exception:
            return True

    @staticmethod
    def _extract_date(html: str, text: str) -> str | None:
        patterns = [
            r'"datePublished"\s*:\s*"([^"]+)"',
            r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|date|pubdate)["\'][^>]+content=["\']([^"\']+)',
            r'(20\d{2})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})日?',
            r'([A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d{2})',
        ]
        hay = (html or "")[:100000] + "\n" + (text or "")[:4000]
        for pattern in patterns:
            match = re.search(pattern, hay, re.I)
            if not match:
                continue
            try:
                raw = (
                    match.group(1)
                    if len(match.groups()) == 1
                    else "-".join(match.groups())
                )
                dt = dateparser.parse(raw, fuzzy=True)
                if dt:
                    return dt.date().isoformat()
            except Exception:
                continue
        return None

    @staticmethod
    def _item_id(url: str, title: str) -> str:
        key = (
            canonical_url(url)
            + "|"
            + title_signature(title)
        ).encode("utf-8")
        return hashlib.sha1(key).hexdigest()[:16]

    def healthcheck(self) -> list[dict[str, Any]]:
        """
        每日轻量健康检查。
        首页响应会缓存，随后 crawl 复用，避免对同一入口重复请求。
        """
        records: list[dict[str, Any]] = []

        for source in self.sources_cfg.get("sources", []):
            if source.get("enabled", True) is False:
                continue

            started = time.time()
            record = {
                "source_id": source["id"],
                "source_name": source["name"],
                "category": source.get("category", ""),
                "source_level": source.get("source_level", "B"),
                "url": source["url"],
                "status": "unknown",
                "http_status": None,
                "robots": "unknown",
                "candidate_links": 0,
                "keyword_hits": 0,
                "latest_visible_date": None,
                "elapsed_ms": 0,
                "error": "",
            }

            try:
                allowed = self._allowed_by_robots(source["url"])
                record["robots"] = "allowed" if allowed else "blocked"
                if not allowed:
                    record["status"] = "robots_blocked"
                    continue

                resp = self._get(
                    source["url"],
                    timeout=self.health_timeout,
                    cache_index=True,
                    retries=1,
                )
                if not resp:
                    record["status"] = "http_failed"
                    record["error"] = "request failed"
                    continue

                record["http_status"] = resp.status_code
                method = source.get("method", "html_index")
                keywords = source.get("include_keywords", [])

                if method == "rss":
                    feed = feedparser.parse(resp.content)
                    hit_count = 0
                    dates = []
                    links = []

                    for entry in feed.entries[: self.max_links]:
                        title = self._clean_text(entry.get("title", ""))
                        snippet = self._clean_text(entry.get("summary", ""))
                        hits = (
                            self._keyword_hits(title + " " + snippet, keywords)
                            if keywords
                            else 1
                        )
                        if keywords and hits == 0:
                            continue

                        hit_count += hits
                        if entry.get("link"):
                            links.append(entry["link"])

                        raw_date = entry.get("published") or entry.get("updated")
                        if raw_date:
                            try:
                                dt = dateparser.parse(raw_date)
                                if dt:
                                    dates.append(dt.date().isoformat())
                            except Exception:
                                pass

                    record["candidate_links"] = len(links)
                    record["keyword_hits"] = hit_count
                    record["latest_visible_date"] = max(dates) if dates else None

                else:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    seen_urls = set()
                    hit_count = 0
                    dates = []

                    for a in soup.find_all("a", href=True):
                        title = self._clean_text(a.get_text(" ", strip=True))
                        if len(title) < 5:
                            continue

                        url = self._normalize_url(
                            source["url"],
                            a.get("href", ""),
                        )
                        if (
                            not url
                            or url in seen_urls
                            or not self._same_domain(source["url"], url)
                        ):
                            continue

                        context = title
                        if a.parent:
                            context += " " + self._clean_text(
                                a.parent.get_text(" ", strip=True)
                            )[:350]

                        hits = (
                            self._keyword_hits(context, keywords)
                            if keywords
                            else 1
                        )
                        if keywords and hits == 0:
                            continue

                        seen_urls.add(url)
                        hit_count += hits
                        date_str = self._extract_date("", context)
                        if date_str:
                            dates.append(date_str)

                    record["candidate_links"] = min(
                        len(seen_urls),
                        self.max_links,
                    )
                    record["keyword_hits"] = hit_count
                    record["latest_visible_date"] = max(dates) if dates else None

                record["status"] = (
                    "ok" if record["candidate_links"] > 0 else "empty"
                )

            except Exception as exc:
                record["status"] = "adapter_needed"
                record["error"] = str(exc)[:300]

            finally:
                record["elapsed_ms"] = int((time.time() - started) * 1000)
                records.append(record)

        self._write_healthcheck(records)
        return records

    def _write_healthcheck(self, records: list[dict[str, Any]]):
        self.health_data_dir.mkdir(parents=True, exist_ok=True)
        self.health_reports_dir.mkdir(parents=True, exist_ok=True)

        (self.health_data_dir / "latest.json").write_text(
            json.dumps(
                {
                    "checked_at": self.today.isoformat(),
                    "sources": records,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        labels = {
            "ok": "✅ OK",
            "empty": "⚠️ Empty",
            "robots_blocked": "🚫 Robots blocked",
            "http_failed": "❌ HTTP failed",
            "adapter_needed": "🧩 Need adapter",
            "unknown": "❔ Unknown",
        }

        counts: dict[str, int] = {}
        for row in records:
            counts[row["status"]] = counts.get(row["status"], 0) + 1

        lines = [
            f"# 信息源健康检查｜{self.today.date().isoformat()}",
            "",
            f"> 检查时间：{self.today.isoformat()}",
            "",
            "## 总览",
            "",
        ]

        for key in labels:
            if counts.get(key):
                lines.append(f"- {labels[key]}：{counts[key]}")

        lines += [
            "",
            "## 明细",
            "",
            "| 状态 | 信息源 | HTTP | Robots | 候选链接 | 关键词命中 | 最新可见日期 | 耗时ms |",
            "|---|---|---:|---|---:|---:|---|---:|",
        ]

        for row in records:
            lines.append(
                f"| {labels.get(row['status'], row['status'])} | "
                f"{row['source_name']} | {row['http_status'] or ''} | "
                f"{row['robots']} | {row['candidate_links']} | "
                f"{row['keyword_hits']} | "
                f"{row['latest_visible_date'] or ''} | "
                f"{row['elapsed_ms']} |"
            )

        bad = [row for row in records if row["status"] != "ok"]
        if bad:
            lines += ["", "## 需要处理", ""]
            for row in bad:
                lines.append(
                    f"- **{row['source_name']}**："
                    f"{row['error'] or row['status']}"
                )

        path = (
            self.health_reports_dir
            / f"{self.today.date().isoformat()}.md"
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Health report: {path}")

    def _crawl_html_index(self, source: dict[str, Any]) -> list[Item]:
        resp = self._get(
            source["url"],
            cache_index=True,
        )
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        keywords = source.get("include_keywords", [])
        candidates = []
        seen_urls: set[str] = set()

        for a in soup.find_all("a", href=True):
            title = self._clean_text(a.get_text(" ", strip=True))
            if len(title) < 5:
                continue

            url = self._normalize_url(
                source["url"],
                a.get("href", ""),
            )
            if (
                not url
                or url in seen_urls
                or not self._same_domain(source["url"], url)
                or canonical_url(url) == canonical_url(source["url"])
            ):
                continue

            context = title
            parent = a.parent
            if parent:
                context += " " + self._clean_text(
                    parent.get_text(" ", strip=True)
                )[:400]

            hits = (
                self._keyword_hits(context, keywords)
                if keywords
                else 1
            )
            if keywords and hits == 0:
                continue

            index_date = self._extract_date("", context)
            if index_date and not self._is_fresh(index_date):
                continue

            score = hits * 10 + min(len(title), 80) / 20
            candidates.append(
                (
                    1 if index_date else 0,
                    index_date or "",
                    float(score),
                    title,
                    url,
                )
            )
            seen_urls.add(url)

        candidates.sort(
            key=lambda row: (row[0], row[1], row[2]),
            reverse=True,
        )

        items: list[Item] = []
        unknown_date_used = 0

        for has_date, _, _, title, url in candidates[: self.max_links]:
            if not has_date and unknown_date_used >= self.max_unknown_date:
                continue

            item_id = self._item_id(url, title)
            if item_id in self.state.get("seen", {}):
                continue

            item = self._fetch_article(source, title, url)
            if item:
                items.append(item)
                if not has_date and not item.published_at:
                    unknown_date_used += 1

            if len(items) >= self.max_articles:
                break

            time.sleep(0.15)

        return items

    def _crawl_rss(self, source: dict[str, Any]) -> list[Item]:
        resp = self._get(
            source["url"],
            cache_index=True,
        )
        if not resp:
            return []

        feed = feedparser.parse(resp.content)
        items: list[Item] = []
        keywords = source.get("include_keywords", [])

        for entry in feed.entries[: self.max_links]:
            title = self._clean_text(entry.get("title", ""))
            url = entry.get("link", "")
            snippet = self._clean_text(entry.get("summary", ""))

            if (
                keywords
                and self._keyword_hits(
                    title + " " + snippet,
                    keywords,
                )
                == 0
            ):
                continue

            raw_date = entry.get("published") or entry.get("updated")
            if raw_date and not self._is_fresh(raw_date):
                continue

            item_id = self._item_id(url, title)
            if item_id in self.state.get("seen", {}):
                continue

            item = self._fetch_article(
                source,
                title,
                url,
                snippet=snippet,
            )
            if item and raw_date:
                try:
                    item.published_at = dateparser.parse(
                        raw_date
                    ).date().isoformat()
                except Exception:
                    pass

            if item:
                items.append(item)

            if len(items) >= self.max_articles:
                break

        return items

    def _fetch_article(
        self,
        source: dict[str, Any],
        title: str,
        url: str,
        snippet: str = "",
    ) -> Item | None:
        text = ""
        published = None

        resp = self._get(url)
        if resp:
            content_type = resp.headers.get("content-type", "")
            if "application/pdf" not in content_type:
                try:
                    extracted = trafilatura.extract(
                        resp.text,
                        include_comments=False,
                        include_tables=True,
                        favor_precision=True,
                    )
                    text = self._clean_text(extracted or "")
                except Exception:
                    pass

                if not text:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    text = self._clean_text(
                        soup.get_text(" ", strip=True)
                    )[:9000]

                published = self._extract_date(resp.text, text)

        if published and not self._is_fresh(published):
            return None

        item_id = self._item_id(url, title)
        return Item(
            id=item_id,
            title=title,
            url=url,
            source_id=source["id"],
            source_name=source["name"],
            source_level=source.get("source_level", "B"),
            source_category=source.get(
                "category",
                "industry_news",
            ),
            source_priority=int(source.get("priority", 3)),
            published_at=published,
            fetched_at=self.today.isoformat(),
            snippet=snippet,
            text=text[:12000],
        )

    def crawl(self) -> list[Item]:
        all_items: list[Item] = []

        for source in self.sources_cfg.get("sources", []):
            if source.get("enabled", True) is False:
                continue

            print(f"\n== {source['name']} ==")
            method = source.get("method", "html_index")

            try:
                if method == "rss":
                    items = self._crawl_rss(source)
                elif method == "html_index":
                    items = self._crawl_html_index(source)
                else:
                    print(
                        f"[source] unsupported method "
                        f"{method}: {source['id']}"
                    )
                    items = []

                print(f"new candidates: {len(items)}")
                all_items.extend(items)

            except Exception as exc:
                print(
                    f"[source error] {source['id']}: {exc}"
                )

        deduped = self._dedupe(all_items)
        self.run_stats["crawled"] = len(deduped)
        return deduped

    @staticmethod
    def _dedupe(items: list[Item]) -> list[Item]:
        out: list[Item] = []

        for item in sorted(
            items,
            key=lambda x: x.source_priority,
            reverse=True,
        ):
            duplicate_index = None
            item_url = canonical_url(item.url)

            for idx, existing in enumerate(out):
                same_url = (
                    item_url
                    and item_url == canonical_url(existing.url)
                )
                same_title = same_event_title(
                    item.title,
                    existing.title,
                )

                if same_url or same_title:
                    duplicate_index = idx
                    break

            if duplicate_index is None:
                out.append(item)
                continue

            existing = out[duplicate_index]
            if item.source_priority > existing.source_priority:
                out[duplicate_index] = item

        return out

    def _yesterday_delivered(self) -> list[dict[str, Any]]:
        yesterday = (
            self.today.date() - timedelta(days=1)
        ).isoformat()
        return [
            row
            for row in self.state.get("delivered", [])
            if row.get("delivered_on") == yesterday
        ]

    def _already_delivered_yesterday(self, item: Item) -> bool:
        item_url = canonical_url(item.url)

        for prev in self._yesterday_delivered():
            prev_url = canonical_url(prev.get("url", ""))

            if item_url and prev_url and item_url == prev_url:
                return True

            if same_event_title(
                item.title,
                prev.get("title", ""),
            ):
                return True

        return False

    def filter_yesterday_duplicates(
        self,
        items: list[Item],
    ) -> list[Item]:
        kept: list[Item] = []
        skipped = 0

        for item in items:
            if self._already_delivered_yesterday(item):
                skipped += 1
            else:
                kept.append(item)

        self.run_stats["deduped_yesterday"] = skipped
        if skipped:
            print(
                f"[delivery dedupe] skipped {skipped} item(s) "
                "already surfaced yesterday"
            )
        return kept

    def _already_delivered_event(self, item: Item) -> bool:
        key = normalize_event_key(item.event_key)
        if not key:
            return False

        for prev in self._yesterday_delivered():
            prev_key = normalize_event_key(
                prev.get("event_key", "")
            )
            if prev_key and prev_key == key:
                return True
        return False

    def filter_event_duplicates(
        self,
        items: list[Item],
    ) -> list[Item]:
        kept = []
        skipped = 0

        for item in items:
            if self._already_delivered_event(item):
                skipped += 1
            else:
                kept.append(item)

        self.run_stats["deduped_event"] = skipped
        if skipped:
            print(
                f"[event dedupe] skipped {skipped} semantic "
                "duplicate(s) from yesterday"
            )
        return kept

    def tag_and_score(self, items: list[Item]) -> list[Item]:
        topics = self.topics_cfg.get("topics", {})
        companies = self.companies_cfg.get("companies", [])
        nearby_events = self._nearby_events()

        for item in items:
            blob = (
                f"{item.title} {item.snippet} "
                f"{item.text[:6000]}"
            )

            item.topics = []
            for key, cfg in topics.items():
                if self._keyword_hits(
                    blob,
                    cfg.get("keywords", []),
                ):
                    item.topics.append(
                        cfg.get("name", key)
                    )

            item.companies = []
            low = blob.lower()
            for company in companies:
                if any(
                    alias.lower() in low
                    for alias in company.get("aliases", [])
                ):
                    item.companies.append(company["name"])

            item.node_score = self._node_score(
                blob,
                nearby_events,
            )
            item.data_score = self._data_score(
                blob,
                item.source_category,
            )
            item.insight_score = self._insight_score(
                blob,
                item.topics or [],
            )

            item.total_score = min(
                15,
                item.node_score
                + item.data_score
                + item.insight_score,
            )
            item.priority = self._priority(
                item.total_score
            )

            item.event_key = (
                "|".join(
                    [
                        (item.companies or [""])[0],
                        (item.topics or [""])[0],
                        title_signature(item.title)[:90],
                    ]
                )
            )

            self._heuristic_analysis(
                item,
                nearby_events,
            )

        return sorted(
            items,
            key=lambda x: (
                x.total_score,
                x.source_priority,
            ),
            reverse=True,
        )

    def _nearby_events(self) -> list[dict[str, Any]]:
        result = []

        for event in self.calendar_cfg.get("events", []):
            try:
                dt = datetime.fromisoformat(
                    str(event["date"])
                ).replace(tzinfo=SH_TZ)
            except Exception:
                continue

            days = (
                dt.date() - self.today.date()
            ).days

            if -1 <= days <= 30:
                row = dict(event)
                row["days_until"] = days
                result.append(row)

        return result

    def _node_score(
        self,
        blob: str,
        events: list[dict[str, Any]],
    ) -> int:
        score = 0

        for event in events:
            hits = self._keyword_hits(
                blob,
                event.get("keywords", []),
            )
            days = event.get("days_until", 99)

            if hits:
                if 0 <= days <= 7:
                    score = max(score, 5)
                elif days <= 14:
                    score = max(score, 4)
                elif days <= 30:
                    score = max(score, 3)

        if re.search(
            r"发布|上市|交付|财报|季度|车展|政策|公告|召回|"
            r"launch|earnings|delivery|recall",
            blob,
            re.I,
        ):
            score = max(score, 2)

        return score

    @staticmethod
    def _data_score(
        blob: str,
        category: str,
    ) -> int:
        nums = re.findall(
            r"(?<!\w)(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
            r"\s*(?:%|万|亿|辆|台|美元|亿元|万辆)?",
            blob,
        )

        score = 0
        if nums:
            score += 2
        if len(nums) >= 4:
            score += 1
        if re.search(
            r"同比|环比|增长|下降|份额|渗透率|排名|销量|交付|"
            r"revenue|margin|year-on-year|market share|deliver",
            blob,
            re.I,
        ):
            score += 1
        if category == "market":
            score += 1

        return min(score, 5)

    @staticmethod
    def _insight_score(
        blob: str,
        topics: list[str],
    ) -> int:
        structural = [
            "趋势",
            "结构",
            "转向",
            "变化",
            "替代",
            "渗透",
            "竞争",
            "高端",
            "价格带",
            "用户",
            "区域",
            "路线",
            "trend",
            "shift",
            "competition",
            "premium",
        ]

        score = min(
            2,
            sum(
                1
                for key in structural
                if key.lower() in blob.lower()
            ),
        )

        if len(topics) >= 2:
            score += 1
        if len(topics) >= 4:
            score += 1
        if re.search(
            r"为什么|意味着|背后|正在|首次|超过|取代|"
            r"why|signals|means|shift",
            blob,
            re.I,
        ):
            score += 1

        return min(score, 5)

    @staticmethod
    def _priority(score: int) -> str:
        if score >= 12:
            return "S"
        if score >= 9:
            return "A"
        if score >= 6:
            return "B"
        return "C"

    def _heuristic_analysis(
        self,
        item: Item,
        events: list[dict[str, Any]],
    ):
        tags = (
            "、".join((item.topics or [])[:4])
            or "行业动态"
        )
        companies = "、".join(
            (item.companies or [])[:3]
        )
        entity = companies or item.source_name

        event_names = [
            event["name"]
            for event in events
            if self._keyword_hits(
                item.title + item.text[:1000],
                event.get("keywords", []),
            )
        ]
        event_text = (
            f"，且临近{'、'.join(event_names[:2])}"
            if event_names
            else ""
        )

        item.why_it_matters = (
            f"这条信息同时涉及{tags}{event_text}，"
            f"可能改变对{entity}或相关赛道的判断。"
        )
        item.trend = self._trend_sentence(item)
        item.brand_relevance = self._brand_relevance(
            item
        )
        item.possible_topics = self._topic_ideas(item)
        item.followup_data = self._followup_data(item)
        item.risk = (
            "如用于传播策划，需回到原始公告、财报或协会口径"
            "核对数字、统计范围与发布时间。"
        )

    @staticmethod
    def _trend_sentence(item: Item) -> str:
        topics = set(item.topics or [])

        if "纯电" in topics and "市场" in topics:
            return (
                "重点观察纯电增长来自总量、价格带、区域"
                "还是用户结构变化，以及是否具有连续性。"
            )
        if "政策" in topics:
            return (
                "重点判断政策改变了谁的购买成本、使用成本"
                "或竞争门槛，并观察实施后的销量反馈。"
            )
        if "财报与经营" in topics:
            return (
                "重点从销量之外继续看毛利、现金流、均价、"
                "投入效率和经营质量。"
            )
        if "技术" in topics:
            return (
                "重点判断这项技术是单点传播，还是已经成为"
                "多家公司共同押注的行业方向。"
            )

        return (
            "需要继续用连续数据和更多一手来源判断它是"
            "单点事件还是结构性变化。"
        )

    @staticmethod
    def _brand_relevance(item: Item) -> str:
        topics = set(item.topics or [])
        parts = []

        if "纯电" in topics:
            parts.append("纯电心智")
        if "高端市场" in topics:
            parts.append("高端品牌")
        if (
            "技术" in topics
            and any(
                key in (item.text + item.title).lower()
                for key in ["换电", "充电", "charging"]
            )
        ):
            parts.append("补能/换电")
        if "政策" in topics:
            parts.append("市场环境与用户购买门槛")

        if not parts:
            parts.append("行业判断与竞品监测")

        return "可用于：" + "、".join(parts[:3]) + "。"

    @staticmethod
    def _topic_ideas(item: Item) -> list[str]:
        topics = set(item.topics or [])
        ideas = []

        if "纯电" in topics and "市场" in topics:
            ideas.append(
                "纯电增长到底来自哪里：总量回暖，还是用户选择发生结构性变化？"
            )
        if "高端市场" in topics:
            ideas.append(
                "高端汽车市场的评价标准正在发生什么变化？"
            )
        if "政策" in topics:
            ideas.append(
                "新政策落地后，新能源车的真实购买成本和竞争格局会怎么变？"
            )
        if "技术" in topics:
            ideas.append(
                "这项技术正在从品牌卖点变成行业标配吗？"
            )
        if "财报与经营" in topics:
            ideas.append(
                "车企竞争进入经营阶段后，除了销量还应该看什么？"
            )
        if item.companies:
            ideas.append(
                f"{item.companies[0]}这次动作，反映了哪一种行业路线正在变强或变弱？"
            )

        if not ideas:
            ideas.append(
                "这条变化是孤立事件，还是下一阶段车市结构变化的早期信号？"
            )

        return ideas[:3]

    @staticmethod
    def _followup_data(item: Item) -> list[str]:
        topics = set(item.topics or [])
        required = [
            "同口径的同比/环比数据",
            "至少连续3个月的趋势数据",
        ]

        if "高端市场" in topics:
            required.append(
                "30/40/50万元以上价格带的品牌与能源结构"
            )
        if "纯电" in topics:
            required.append(
                "纯电、增程、插混在对应价格带与区域的份额"
            )
        if "政策" in topics:
            required.append(
                "政策生效前后的终端销量/订单变化"
            )
        if item.companies:
            required.append("主要竞品同期表现")

        return required[:4]

    def llm_enrich(
        self,
        items: list[Item],
    ) -> list[Item]:
        api_key = (
            os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("LLM_API_KEY")
        )
        if not api_key:
            print(
                "[LLM] API key not configured; "
                "using heuristic analysis"
            )
            return items

        base_url = (
            os.getenv("LLM_BASE_URL")
            or "https://api.deepseek.com"
        )
        model = (
            os.getenv("LLM_MODEL")
            or "deepseek-v4-flash"
        )

        endpoint = base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"

        candidates = [
            item
            for item in items
            if item.priority in ("S", "A", "B")
        ][:16]

        by_id = {item.id: item for item in items}

        for start in range(0, len(candidates), 8):
            batch = candidates[start:start + 8]
            payload_items = []

            for item in batch:
                payload_items.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "source": item.source_name,
                        "source_level": item.source_level,
                        "source_category": item.source_category,
                        "url": item.url,
                        "topics": item.topics,
                        "companies": item.companies,
                        "published_at": item.published_at,
                        "text": item.text[:2400],
                    }
                )

            system_prompt = (
                "你是汽车品牌传播团队的研究编辑。"
                "只根据输入事实分析，不补造数据。"
                "目标是判断信息对传播选题的价值。"
                "必须输出合法 JSON。格式示例："
                '{"results":[{"id":"xxx","event_key":"主体|事件|时间",'
                '"why_it_matters":"", "trend":"",'
                '"possible_topics":[""],"followup_data":[""],'
                '"risk":"","node_score":0,"data_score":0,'
                '"insight_score":0}]}。'
                "每项评分0-5。节点=明确时点或事件由头；"
                "数据=存在可验证的量化变化；"
                "洞察=能解释结构、人群、消费或竞争变化。"
                "event_key用于识别同一事件不同标题，"
                "必须尽量稳定、简短，并包含主体、动作/变化和时间周期。"
            )

            payload: dict[str, Any] = {
                "model": model,
                "temperature": 0.2,
                "max_tokens": 3200,
                "response_format": {
                    "type": "json_object"
                },
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "items": payload_items
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            }

            if "deepseek.com" in base_url:
                payload["thinking"] = {
                    "type": "disabled"
                }

            obj = None
            for attempt in range(2):
                try:
                    resp = self.session.post(
                        endpoint,
                        headers={
                            "Authorization": (
                                f"Bearer {api_key}"
                            ),
                            "Content-Type": (
                                "application/json"
                            ),
                        },
                        json=payload,
                        timeout=120,
                    )
                    resp.raise_for_status()
                    content = (
                        resp.json()["choices"][0]
                        ["message"]["content"]
                    )

                    if not content:
                        raise ValueError(
                            "empty LLM content"
                        )

                    obj = json.loads(content)
                    break

                except Exception as exc:
                    print(
                        f"[LLM batch failed "
                        f"{start // 8 + 1}, "
                        f"attempt {attempt + 1}] {exc}"
                    )
                    time.sleep(2)

            if not isinstance(obj, dict):
                continue

            for result in obj.get("results", []):
                item = by_id.get(str(result.get("id", "")))
                if not item:
                    continue

                item.event_key = result.get(
                    "event_key",
                    item.event_key,
                )
                item.why_it_matters = result.get(
                    "why_it_matters",
                    item.why_it_matters,
                )
                item.trend = result.get(
                    "trend",
                    item.trend,
                )

                ideas = result.get(
                    "possible_topics",
                    item.possible_topics,
                )
                if isinstance(ideas, list):
                    item.possible_topics = ideas[:3]

                followups = result.get(
                    "followup_data",
                    item.followup_data,
                )
                if isinstance(followups, list):
                    item.followup_data = followups[:4]

                item.risk = result.get(
                    "risk",
                    item.risk,
                )

                for field in (
                    "node_score",
                    "data_score",
                    "insight_score",
                ):
                    try:
                        value = max(
                            0,
                            min(
                                5,
                                int(
                                    result.get(
                                        field,
                                        getattr(item, field),
                                    )
                                ),
                            ),
                        )
                        setattr(item, field, value)
                    except Exception:
                        pass

                item.total_score = min(
                    15,
                    item.node_score
                    + item.data_score
                    + item.insight_score,
                )
                item.priority = self._priority(
                    item.total_score
                )

        return sorted(
            items,
            key=lambda x: (
                x.total_score,
                x.source_priority,
            ),
            reverse=True,
        )

    def select_report_items(
        self,
        items: list[Item],
    ) -> dict[str, list[Item]]:
        used: set[str] = set()

        def pick(predicate, limit):
            chosen = []
            for item in items:
                if item.id in used:
                    continue
                if predicate(item):
                    chosen.append(item)
                    used.add(item.id)
                if len(chosen) >= limit:
                    break
            return chosen

        high = pick(
            lambda x: x.priority in ("S", "A"),
            5,
        )
        market = pick(
            lambda x: x.source_category == "market",
            5,
        )
        competitor = pick(
            lambda x: x.source_category == "competitor",
            5,
        )
        policy_industry = pick(
            lambda x: x.source_category
            in ("policy", "industry_news"),
            5,
        )

        ideas = [
            item
            for item in items
            if item.total_score >= 8
        ][:3]

        return {
            "high": high,
            "market": market,
            "competitor": competitor,
            "policy_industry": policy_industry,
            "ideas": ideas,
        }

    @staticmethod
    def _unique_items(
        selection: dict[str, list[Item]],
    ) -> list[Item]:
        out = []
        seen = set()

        for key in (
            "high",
            "market",
            "competitor",
            "policy_industry",
            "ideas",
        ):
            for item in selection.get(key, []):
                if item.id not in seen:
                    seen.add(item.id)
                    out.append(item)

        return out

    def _render_report(
        self,
        items: list[Item],
        selection: dict[str, list[Item]],
    ) -> str:
        events = self._nearby_events()

        lines = [
            f"# 汽车传播 Radar｜"
            f"{self.today.date().isoformat()}",
            "",
            "> 自动监测公开信息源。正式用于传播前，"
            "重要数据仍需回到一手页面核对。",
            "",
        ]

        if events:
            lines += ["## 临近节点", ""]
            for event in events:
                days = event["days_until"]
                when = (
                    "今天"
                    if days == 0
                    else (
                        f"还有 {days} 天"
                        if days > 0
                        else "进行中/刚结束"
                    )
                )
                lines.append(
                    f"- **{event['name']}**：{when}；"
                    f"建议关注："
                    f"{' / '.join(event.get('keywords', []))}"
                )
            lines.append("")

        self._section(
            lines,
            "01 今日必须知道",
            selection["high"],
        )
        self._section(
            lines,
            "02 市场变化",
            selection["market"],
        )
        self._section(
            lines,
            "03 竞品动作",
            selection["competitor"],
        )
        self._section(
            lines,
            "04 政策 / 行业变量",
            selection["policy_industry"],
        )

        lines += ["## 05 潜在传播选题", ""]

        if not selection["ideas"]:
            lines.append(
                "今天没有达到候选阈值的重点选题。"
            )

        for idx, item in enumerate(
            selection["ideas"],
            1,
        ):
            topic = (
                item.possible_topics
                or [item.title]
            )[0]

            lines += [
                f"### {idx}. {topic}",
                "",
                f"**触发事件**："
                f"[{item.title}]({item.url})",
                "",
                f"**来源**：{item.source_name}"
                f"（{item.source_level}级）",
                "",
                f"**评分**：节点 {item.node_score}/5｜"
                f"数据 {item.data_score}/5｜"
                f"洞察 {item.insight_score}/5｜"
                f"总分 {item.total_score}/15"
                f"（{item.priority}）",
                "",
                f"**为什么值得关注**："
                f"{item.why_it_matters}",
                "",
                f"**趋势判断**：{item.trend}",
                "",
                f"**与传播线的关系**："
                f"{item.brand_relevance}",
                "",
                "**建议继续补的数据**：",
            ]

            for row in item.followup_data or []:
                lines.append(f"- {row}")

            lines += [
                "",
                f"**风险**：{item.risk}",
                "",
            ]

        surfaced = len(
            self._unique_items(selection)
        )
        self.run_stats["surfaced"] = surfaced

        lines += [
            "---",
            "",
            f"本次抓取新增 {len(items)} 条；"
            f"实际进入日报 {surfaced} 条；"
            f"昨日重复过滤 "
            f"{self.run_stats['deduped_yesterday']} 条；"
            f"事件级重复过滤 "
            f"{self.run_stats['deduped_event']} 条。",
        ]

        return "\n".join(lines) + "\n"

    @staticmethod
    def _section(
        lines: list[str],
        title: str,
        items: list[Item],
    ):
        lines += [f"## {title}", ""]

        if not items:
            lines += [
                "暂无新增高相关信息。",
                "",
            ]
            return

        for item in items:
            tags = " / ".join(
                (item.topics or [])[:4]
            )

            lines += [
                f"### [{item.title}]({item.url})",
                f"- 来源：{item.source_name}"
                f"（{item.source_level}级）",
                f"- 标签："
                f"{tags or '行业动态'}",
                f"- 评分：节点 {item.node_score}｜"
                f"数据 {item.data_score}｜"
                f"洞察 {item.insight_score}｜"
                f"**{item.total_score}/15 "
                f"{item.priority}级**",
                f"- 判断：{item.why_it_matters}",
                "",
            ]

    def _render_feishu_markdown(
        self,
        selection: dict[str, list[Item]],
    ) -> str:
        sections = [
            ("今日必须知道", selection["high"][:3]),
            ("市场变化", selection["market"][:3]),
            ("竞品动作", selection["competitor"][:3]),
            (
                "政策 / 行业变量",
                selection["policy_industry"][:3],
            ),
        ]

        lines = []

        for title, items in sections:
            if not items:
                continue

            lines.append(f"### {title}")
            for item in items:
                lines.append(
                    f"- [{item.title}]({item.url})"
                )
                lines.append(
                    f"  {item.source_name}｜"
                    f"{item.total_score}/15 "
                    f"{item.priority}级"
                )
            lines.append("")

        if selection["ideas"]:
            lines.append("### 潜在传播选题")
            for idx, item in enumerate(
                selection["ideas"],
                1,
            ):
                idea = (
                    item.possible_topics
                    or [item.title]
                )[0]
                lines.append(
                    f"{idx}. **{idea}**"
                )
                lines.append(
                    f"   触发："
                    f"[{item.title}]({item.url})"
                )

        if not lines:
            lines.append(
                "今天没有新增高相关信息。"
            )

        return "\n".join(lines)

    def _persist_items(
        self,
        items: list[Item],
    ):
        self.data_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        with self.items_path.open(
            "a",
            encoding="utf-8",
        ) as f:
            for item in items:
                f.write(
                    json.dumps(
                        asdict(item),
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        for item in items:
            self.state.setdefault(
                "seen",
                {},
            )[item.id] = {
                "title": item.title,
                "url": item.url,
                "seen_at": self.today.isoformat(),
            }

    def _mark_delivered(
        self,
        items: list[Item],
    ):
        for item in items:
            self.state.setdefault(
                "delivered",
                [],
            ).append(
                {
                    "item_id": item.id,
                    "title": item.title,
                    "url": item.url,
                    "event_key": item.event_key,
                    "delivered_on": (
                        self.today.date().isoformat()
                    ),
                }
            )

    def _prune_state(self):
        cutoff = self.today - timedelta(days=120)
        seen_pruned = {}

        for key, value in self.state.get(
            "seen",
            {},
        ).items():
            try:
                dt = dateparser.parse(
                    value.get("seen_at", "")
                )
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=SH_TZ)

                if not dt or dt >= cutoff:
                    seen_pruned[key] = value
            except Exception:
                seen_pruned[key] = value

        self.state["seen"] = seen_pruned

        delivery_cutoff = (
            self.today.date()
            - timedelta(days=30)
        )

        delivered_pruned = []
        for row in self.state.get(
            "delivered",
            [],
        ):
            try:
                d = dateparser.parse(
                    row.get("delivered_on", "")
                ).date()
                if d >= delivery_cutoff:
                    delivered_pruned.append(row)
            except Exception:
                delivered_pruned.append(row)

        self.state["delivered"] = delivered_pruned

    def write_and_deliver(
        self,
        items: list[Item],
    ):
        self.daily_reports_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        selection = self.select_report_items(items)
        report = self._render_report(
            items,
            selection,
        )
        report_path = (
            self.daily_reports_dir
            / f"{self.today.date().isoformat()}.md"
        )
        report_path.write_text(
            report,
            encoding="utf-8",
        )
        print(f"\nReport: {report_path}")

        surfaced = self._unique_items(selection)

        webhook = os.getenv(
            "FEISHU_WEBHOOK_URL"
        )
        signing_secret = os.getenv(
            "FEISHU_SIGNING_SECRET"
        )
        send_empty = (
            os.getenv(
                "FEISHU_SEND_EMPTY",
                "false",
            ).lower()
            in ("1", "true", "yes")
        )

        feishu_success = None

        if webhook and (surfaced or send_empty):
            markdown = self._render_feishu_markdown(
                selection
            )
            feishu_success, detail = send_feishu(
                webhook,
                title=(
                    "汽车传播 Radar｜"
                    f"{self.today.date().isoformat()}"
                ),
                markdown=markdown,
                signing_secret=signing_secret,
            )

            if feishu_success:
                print("[Feishu] sent successfully")
            else:
                print(
                    f"[Feishu] send failed: {detail}"
                )

        elif webhook:
            print(
                "[Feishu] no surfaced items; "
                "empty message skipped"
            )
        else:
            print(
                "[Feishu] webhook not configured; "
                "report only"
            )

        # webhook 已配置且发送失败：
        # 不写 seen / delivered，让下一次运行还能重新抓取并重试。
        if webhook and feishu_success is False:
            raise RuntimeError(
                "Feishu delivery failed; state not advanced"
            )

        self._persist_items(items)

        # delivered 只记录真正进入日报/飞书的项目，
        # 不再把所有抓到的内容都标记为“已发”。
        self._mark_delivered(surfaced)
        self._prune_state()
        self._save_state()

    def run(
        self,
        no_llm: bool = False,
        max_items: int = 120,
        skip_healthcheck: bool = False,
        healthcheck_only: bool = False,
    ):
        if not skip_healthcheck:
            self.healthcheck()

        if healthcheck_only:
            return

        items = self.crawl()
        items = items[:max_items]

        items = self.filter_yesterday_duplicates(
            items
        )
        items = self.tag_and_score(items)

        if not no_llm:
            items = self.llm_enrich(items)

        # LLM event_key 二次判断：
        # 同一事件换了完全不同标题，也能避免第二天重复发送。
        items = self.filter_event_duplicates(
            items
        )

        self.write_and_deliver(items)


def main():
    parser = argparse.ArgumentParser(
        description="Automotive PR topic radar"
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable optional LLM enrichment",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=120,
        help="Hard cap after dedupe",
    )
    parser.add_argument(
        "--skip-healthcheck",
        action="store_true",
        help="Skip source health check",
    )
    parser.add_argument(
        "--healthcheck-only",
        action="store_true",
        help="Only run source health check",
    )
    args = parser.parse_args()

    radar = Radar()
    radar.run(
        no_llm=args.no_llm,
        max_items=args.max_items,
        skip_healthcheck=args.skip_healthcheck,
        healthcheck_only=args.healthcheck_only,
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, List
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests

INDIA_TZ = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class MacroEvent:
    title: str
    source: str
    region: str
    impact: str = "high"
    when: str | None = None
    url: str | None = None

    def summary_line(self) -> str:
        when_text = f" | {self.when}" if self.when else ""
        return f"{self.region} | {self.source} | {self.impact.upper()} | {self.title}{when_text}"


class MacroNewsManager:
    """Collect fresh, macros-relevant market headlines and summarize them."""

    HIGH_IMPACT_KEYWORDS = (
        "rbi",
        "fed",
        "fomc",
        "repo",
        "rate",
        "inflation",
        "cpi",
        "pce",
        "gdp",
        "jobs",
        "nfp",
        "bond yield",
        "oil",
        "usd",
        "dollar",
        "policy",
        "bank",
        "growth",
        "commodity",
    )

    RSS_SOURCES = (
        (
            "India",
            "https://news.google.com/rss/search?q=RBI+OR+inflation+India+market+news&hl=en-IN&gl=IN&ceid=IN:en",
        ),
        (
            "US",
            "https://news.google.com/rss/search?q=Fed+OR+FOMC+OR+inflation+US+market+news&hl=en-US&gl=US&ceid=US:en",
        ),
        (
            "Global",
            "https://news.google.com/rss/search?q=oil+OR+bond+yields+OR+USD+market+news&hl=en-US&gl=US&ceid=US:en",
        ),
    )

    FALLBACK_EVENTS = (
        MacroEvent(
            title="RBI MPC / repo rate decision watch",
            source="RBI",
            region="India",
            impact="high",
            when="Today",
            url="https://rbi.org.in",
        ),
        MacroEvent(
            title="US Fed FOMC / rate policy watch",
            source="Fed",
            region="US",
            impact="high",
            when="Today",
            url="https://www.federalreserve.gov",
        ),
        MacroEvent(
            title="US CPI / inflation print watch",
            source="US data",
            region="US",
            impact="high",
            when="Today",
            url="https://www.bls.gov",
        ),
        MacroEvent(
            title="Crude oil / commodity and USDINR sensitivity",
            source="Global markets",
            region="Global",
            impact="medium",
            when="Today",
            url="https://www.investing.com",
        ),
    )

    @staticmethod
    def _normalize_dt(value: Any) -> datetime:
        if value is None:
            return datetime.now(INDIA_TZ)
        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=INDIA_TZ)
            return dt.astimezone(INDIA_TZ)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=INDIA_TZ)
            return parsed.astimezone(INDIA_TZ)
        return datetime.now(INDIA_TZ)

    @staticmethod
    def _is_macro_relevant(title: str) -> bool:
        text = title.lower()
        if any(token in text for token in ("cricket", "football", "ipl", "movie")):
            return False
        return any(keyword in text for keyword in MacroNewsManager.HIGH_IMPACT_KEYWORDS)

    @staticmethod
    def _safe_get(url: str, timeout: int = 10) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            )
        }
        response = requests.get(url, timeout=timeout, headers=headers)
        if response.ok:
            return response.text
        return ""

    def _parse_rss_items(self, text: str, region: str) -> List[MacroEvent]:
        if not text:
            return []

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []

        items: List[MacroEvent] = []
        for node in root.iter("item"):
            title = (node.findtext("title") or "").strip()
            if not title:
                continue
            if not self._is_macro_relevant(title):
                continue
            items.append(
                MacroEvent(
                    title=re.sub(r"\s+", " ", title),
                    source="news feed",
                    region=region,
                    impact=(
                        "high"
                        if any(
                            word in title.lower()
                            for word in (
                                "rbi",
                                "fed",
                                "fomc",
                                "rate",
                                "policy",
                                "inflation",
                                "cpi",
                                "gdp",
                            )
                        )
                        else "medium"
                    ),
                    when="Today",
                    url=(node.findtext("link") or "").strip(),
                )
            )
        return items

    def fetch_today_events(self) -> List[MacroEvent]:
        events: List[MacroEvent] = []
        seen: set[str] = set()

        for region, url in self.RSS_SOURCES:
            payload = self._safe_get(url)
            for item in self._parse_rss_items(payload, region):
                key = (item.title.lower(), item.region.lower())
                if key in seen:
                    continue
                seen.add(key)
                events.append(item)

        if events:
            return events[:6]
        return list(self.FALLBACK_EVENTS)

    def should_emit_summary(self, current_time: datetime | None = None) -> bool:
        now = self._normalize_dt(current_time)
        return now.hour > 15 or (now.hour == 15 and now.minute >= 30)

    def format_summary(self, stage: str = "STARTUP") -> str:
        events = self.fetch_today_events()
        lines = [
            "",
            "=" * 70,
            f"AIMIOS MACRO NEWS SUMMARY | {stage}",
            "=" * 70,
        ]

        if not events:
            lines.append("No high-impact macro events detected today.")
        else:
            for idx, event in enumerate(events[:6], start=1):
                lines.append(f"{idx}. {event.summary_line()}")

        lines.append(
            "IMPACT FOCUS: RBI, Fed, inflation, jobs, crude oil, USDINR, bond yields"
        )
        lines.append("=" * 70)
        return "\n".join(lines)

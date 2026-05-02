"""Новости из RSS и Google News (публичные ленты)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import quote_plus

import feedparser
import requests

from .config import DEFAULT_NEWS_FEEDS, GOOGLE_NEWS_RSS_TEMPLATE


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published_ts: float | None = None  # unix time, для затухания по давности


def _fetch_rss(url: str, timeout: float = 12.0, retries: int = 2) -> list[NewsItem]:
    headers = {
        "User-Agent": "StockSignalAnalyzer/0.1 (+https://example.local)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    out: list[NewsItem] = []
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 429:
                wait = 1.5 * (attempt + 1)
                time.sleep(wait)
                continue
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            break
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    else:
        return out  # все попытки исчерпаны
    for e in getattr(parsed, "entries", [])[:40]:
        title = getattr(e, "title", "") or ""
        link = getattr(e, "link", "") or ""
        src = getattr(getattr(e, "source", None), "title", None) or parsed.feed.get("title", url)
        pub = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        ts: float | None = None
        if pub:
            try:
                ts = time.mktime(pub)
            except (TypeError, OverflowError, OSError):
                ts = None
        if title:
            out.append(
                NewsItem(title=title.strip(), link=link, source=str(src)[:80], published_ts=ts)
            )
    return out


def fetch_ticker_news_google(symbol: str, company_hint: str = "") -> list[NewsItem]:
    q = f"{symbol} stock"
    if company_hint:
        q = f"{symbol} {company_hint}"
    url = GOOGLE_NEWS_RSS_TEMPLATE.format(query=quote_plus(q))
    return _fetch_rss(url)


def fetch_macro_headlines(feed_urls: Iterable[str] | None = None) -> list[NewsItem]:
    feeds = list(feed_urls) if feed_urls else DEFAULT_NEWS_FEEDS
    merged: list[NewsItem] = []
    seen: set[str] = set()
    for u in feeds:
        for it in _fetch_rss(u):
            key = re.sub(r"\s+", " ", it.title.lower())
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
        time.sleep(0.15)
    return merged[:50]

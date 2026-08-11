from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, UTC
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import feedparser
import httpx
from loguru import logger

from app.config import settings
from app.domain.entities.rss_seen_item import RssSeenItem
from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository

DEFAULT_NEWS_RSS: dict[str, Any] = {
    "enabled": False,
    "feeds": [],
    "sites": [],
    "mode": "on_new",
    "poll_interval_minutes": 5,
    "max_age_hours": 24,
    "max_posts_per_hour": 3,
    "publish_from_msk": "09:00",
    "publish_until_msk": "22:00",
    "niche": "",
    "topic_brief": "",
    "include_keywords": [],
    "exclude_keywords": [],
    "keywords_source": "",
}

MSK_OFFSET_HOURS = 3
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

NICHE_LABELS: dict[str, str] = {
    "crypto": "Крипта",
    "it": "IT / технологии",
    "politics": "Политика",
    "general": "Общее",
    "custom": "Своя тема",
}

# Fallback if OpenAI fails
NICHE_PRESETS: dict[str, dict[str, list[str]]] = {
    "crypto": {
        "include": [
            "bitcoin", "ethereum", "crypto", "blockchain", "btc", "eth",
            "сек", "sec", "etf", "биткоин", "крипт", "токен", "defi",
        ],
        "exclude": [
            "giveaway", "airdrop", "promo", "промокод", "розыгрыш",
            "сигнал", "signals", "реклама",
        ],
    },
    "it": {
        "include": [
            "ai", "openai", "google", "apple", "microsoft", "cybersecurity",
            "software", "startup", "ии", "нейросет", "технолог", "гаджет",
        ],
        "exclude": ["coupon", "hiring", "вакансия", "скидка", "промокод"],
    },
    "politics": {
        "include": [
            "правил", "закон", "правительств", "выборы", "санкц",
            "президент", "парламент", "мид", "nato", "ес ",
        ],
        "exclude": ["гороскоп", "реклама", "розыгрыш", "промокод"],
    },
    "general": {
        "include": [],
        "exclude": ["розыгрыш", "промокод", "giveaway", "airdrop", "реклама"],
    },
}


@dataclass
class RssNewsItem:
    feed_url: str
    guid: str
    url: str
    title: str
    summary: str
    published_at: datetime | None = None
    image_url: str | None = None

    def to_meta(self) -> dict[str, Any]:
        data = asdict(self)
        if self.published_at is not None:
            data["published_at"] = self.published_at.isoformat()
        return data

    def to_brief(self) -> str:
        parts = [f"Заголовок: {self.title}"]
        if self.published_at:
            parts.append(
                f"Дата: {self.published_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}"
            )
        if self.summary:
            parts.append(f"Кратко: {self.summary[:1500]}")
        if self.url:
            parts.append(f"Источник: {self.url}")
        return "\n".join(parts)

    def card_text(self) -> str:
        lines = [f"**{self.title}**"] if self.title else []
        if self.summary:
            lines.append("")
            lines.append(self.summary[:1200])
        if self.url:
            lines.append("")
            lines.append(f"Источник: {self.url}")
        return "\n".join(lines).strip() or (self.url or "Новость")

    def haystack(self) -> str:
        return f"{self.title} {self.summary}".casefold()


def _clean_keyword_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        word = str(item or "").strip()
        if not word:
            continue
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
    return out[:40]


def parse_hhmm(value: Any, *, default: str) -> str:
    """Return normalized HH:MM or ``default`` if invalid."""
    raw = str(value or "").strip()
    match = _HHMM_RE.match(raw)
    if not match:
        return default
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return default
    return f"{hour:02d}:{minute:02d}"


_WINDOW_RANGE_RE = re.compile(
    r"^\s*(\d{1,2})[:.](\d{2})\s*(?:[-–—]|до|to)\s*(\d{1,2})[:.](\d{2})\s*$",
    re.IGNORECASE,
)


def parse_publish_window_text(text: str) -> tuple[str, str] | None:
    """Parse custom window like ``09:00-22:00`` / ``9.00 – 22.00`` → (from, until)."""
    raw = (text or "").strip()
    if not raw:
        return None
    # Allow "с 09:00 до 22:00"
    cleaned = re.sub(r"^(с|from)\s+", "", raw, flags=re.IGNORECASE)
    match = _WINDOW_RANGE_RE.match(cleaned)
    if not match:
        return None
    h1, m1, h2, m2 = (int(match.group(i)) for i in range(1, 5))
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"


def _minutes_since_midnight(hhmm: str) -> int:
    parts = hhmm.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def is_within_publish_window(
    now_utc: datetime,
    from_msk: str,
    until_msk: str,
) -> bool:
    """True if ``now_utc`` falls in [from_msk, until_msk) Moscow time.

    If from == until, publishing is allowed 24/7.
    Window does not wrap past midnight (MVP).
    """
    from_s = parse_hhmm(from_msk, default="09:00")
    until_s = parse_hhmm(until_msk, default="22:00")
    from_m = _minutes_since_midnight(from_s)
    until_m = _minutes_since_midnight(until_s)
    if from_m == until_m:
        return True

    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    else:
        now_utc = now_utc.astimezone(UTC)
    msk = now_utc + timedelta(hours=MSK_OFFSET_HOURS)
    now_m = msk.hour * 60 + msk.minute
    return from_m <= now_m < until_m


def format_publish_window_label(from_msk: str, until_msk: str) -> str:
    from_s = parse_hhmm(from_msk, default="09:00")
    until_s = parse_hhmm(until_msk, default="22:00")
    if from_s == until_s:
        return "круглосуточно"
    return f"{from_s}–{until_s} МСК"


def normalize_news_rss(raw: Any) -> dict[str, Any]:
    data = dict(DEFAULT_NEWS_RSS)
    if not isinstance(raw, dict):
        return data
    data["enabled"] = bool(raw.get("enabled", False))
    feeds = raw.get("feeds") or []
    if isinstance(feeds, list):
        data["feeds"] = [str(u).strip() for u in feeds if str(u).strip()]
    sites = raw.get("sites") or []
    if isinstance(sites, list):
        data["sites"] = [str(u).strip() for u in sites if str(u).strip()]
    data["mode"] = str(raw.get("mode") or "on_new")
    try:
        data["poll_interval_minutes"] = max(1, int(raw.get("poll_interval_minutes", 5)))
    except (TypeError, ValueError):
        data["poll_interval_minutes"] = 5
    try:
        data["max_age_hours"] = max(1, int(raw.get("max_age_hours", 24)))
    except (TypeError, ValueError):
        data["max_age_hours"] = 24
    try:
        # 0 = unlimited (no hourly cap)
        data["max_posts_per_hour"] = max(0, int(raw.get("max_posts_per_hour", 3)))
    except (TypeError, ValueError):
        data["max_posts_per_hour"] = 3
    data["publish_from_msk"] = parse_hhmm(
        raw.get("publish_from_msk"), default=str(DEFAULT_NEWS_RSS["publish_from_msk"])
    )
    data["publish_until_msk"] = parse_hhmm(
        raw.get("publish_until_msk"), default=str(DEFAULT_NEWS_RSS["publish_until_msk"])
    )
    niche = str(raw.get("niche") or "").strip()
    data["niche"] = niche if niche in NICHE_LABELS or niche == "" else "custom"
    data["topic_brief"] = str(raw.get("topic_brief") or "").strip()[:500]
    data["include_keywords"] = _clean_keyword_list(raw.get("include_keywords"))
    data["exclude_keywords"] = _clean_keyword_list(raw.get("exclude_keywords"))
    src = str(raw.get("keywords_source") or "").strip()
    data["keywords_source"] = src if src in ("ai", "preset", "manual", "") else ""
    return data


def is_rss_trigger(blocks_config: dict[str, Any] | None) -> bool:
    news = normalize_news_rss((blocks_config or {}).get("news_rss"))
    has_sources = bool(news["feeds"] or news["sites"])
    return bool(news["enabled"] and has_sources and news.get("mode") == "on_new")


def preset_keywords(niche: str) -> dict[str, list[str]]:
    base = NICHE_PRESETS.get(niche) or NICHE_PRESETS["general"]
    return {
        "include": list(base["include"]),
        "exclude": list(base["exclude"]),
    }


def _parse_struct_time(value: struct_time | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime(*value[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _parse_entry_date(entry: Any) -> datetime | None:
    published = _parse_struct_time(getattr(entry, "published_parsed", None))
    if published:
        return published
    updated = _parse_struct_time(getattr(entry, "updated_parsed", None))
    if updated:
        return updated
    for key in ("published", "updated"):
        raw = entry.get(key) if hasattr(entry, "get") else getattr(entry, key, None)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
    return None


def _looks_like_image_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw or len(raw) < 12:
        return False
    low = raw.casefold()
    if low.startswith("data:") or "svg" in low.split("?")[0][-4:]:
        return False
    if not (low.startswith("http://") or low.startswith("https://")):
        return False
    return True


def _first_img_src(html: str) -> str | None:
    match = re.search(
        r"""<img\b[^>]*\bsrc=["']([^"']+)["']""",
        html or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip() or None


def extract_entry_image_url(entry: Any, *, page_url: str = "") -> str | None:
    """Pull image URL from feedparser entry media/enclosure/html summary."""
    for media in getattr(entry, "media_content", None) or []:
        if not isinstance(media, dict):
            continue
        url = (media.get("url") or "").strip()
        typ = (media.get("type") or "").casefold()
        medium = (media.get("medium") or "").casefold()
        if url and (typ.startswith("image") or medium == "image" or _looks_like_image_url(url)):
            if _looks_like_image_url(url):
                return urljoin(page_url, url) if page_url else url

    for thumb in getattr(entry, "media_thumbnail", None) or []:
        if not isinstance(thumb, dict):
            continue
        url = (thumb.get("url") or "").strip()
        if _looks_like_image_url(url):
            return urljoin(page_url, url) if page_url else url

    for enc in getattr(entry, "enclosures", None) or []:
        if not isinstance(enc, dict):
            continue
        url = (enc.get("href") or enc.get("url") or "").strip()
        typ = (enc.get("type") or "").casefold()
        if url and (typ.startswith("image") or _looks_like_image_url(url)):
            if _looks_like_image_url(url):
                return urljoin(page_url, url) if page_url else url

    for key in ("summary", "description", "content"):
        raw = entry.get(key) if hasattr(entry, "get") else getattr(entry, key, None)
        if isinstance(raw, list) and raw:
            raw = raw[0].get("value") if isinstance(raw[0], dict) else raw[0]
        html = raw if isinstance(raw, str) else ""
        if "<img" not in html.casefold():
            continue
        src = _first_img_src(html)
        if src and _looks_like_image_url(urljoin(page_url, src) if page_url else src):
            return urljoin(page_url, src) if page_url else src
    return None


_OG_IMAGE_RE = re.compile(
    r"""<meta[^>]+(?:property|name)=["'](?:og:image|twitter:image)["'][^>]+content=["']([^"']+)["']""",
    re.IGNORECASE,
)
_OG_IMAGE_RE_ALT = re.compile(
    r"""<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["'](?:og:image|twitter:image)["']""",
    re.IGNORECASE,
)


def extract_og_image(html: str, *, page_url: str = "") -> str | None:
    for pattern in (_OG_IMAGE_RE, _OG_IMAGE_RE_ALT):
        match = pattern.search(html or "")
        if not match:
            continue
        src = match.group(1).strip()
        abs_url = urljoin(page_url, src) if page_url else src
        if _looks_like_image_url(abs_url):
            return abs_url
    return None


def _rss_http_proxy() -> str | None:
    return (settings.rss.http_proxy or "").strip() or None


_RSS_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}
_RETRYABLE = (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError, httpx.ReadTimeout)
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "yclid",
        "mc_cid",
        "mc_eid",
        "_openstat",
        "ysclid",
    }
)


def normalize_item_url(url: str) -> str:
    """Canonicalize article URL for dedup (host case, slash, tracking params)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return raw.split("#")[0].strip()
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        low = key.casefold()
        if low in _TRACKING_PARAMS or any(low.startswith(p) for p in _TRACKING_PARAM_PREFIXES):
            continue
        pairs.append((key, value))
    query = urlencode(pairs)
    return urlunparse((scheme, netloc, path, "", query, ""))


def title_dedup_key(title: str) -> str:
    """Normalize title for cross-source duplicate detection."""
    text = (title or "").casefold()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def _is_vk_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host in ("vk.com", "vk.ru", "m.vk.com", "m.vk.ru") or host.endswith(
        (".vk.com", ".vk.ru")
    )


def _is_ssl_verify_error(exc: BaseException) -> bool:
    msg = str(exc).casefold()
    if "certificate" in msg or "ssl" in msg:
        return True
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, BaseException):
        return _is_ssl_verify_error(cause)
    return False


def _http_client(timeout: float, *, verify: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        proxy=_rss_http_proxy(),
        verify=verify,
        headers=_RSS_UA,
    )


async def _http_get_with_retry(
    url: str,
    *,
    timeout: float = 20.0,
    attempts: int = 3,
) -> httpx.Response:
    """GET with retries for flaky Beget proxy / network blips."""
    import asyncio

    last_exc: Exception | None = None
    use_verify = True
    ssl_soft_retried = False
    attempt = 0
    while attempt < attempts:
        attempt += 1
        try:
            async with _http_client(timeout, verify=use_verify) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp
        except Exception as exc:
            if use_verify and _is_ssl_verify_error(exc) and not ssl_soft_retried:
                ssl_soft_retried = True
                use_verify = False
                logger.warning(
                    f"RSS SSL verify failed, retrying insecure url={url}: {exc}"
                )
                attempt -= 1  # do not consume a normal attempt for SSL soft retry
                continue
            if isinstance(exc, _RETRYABLE):
                last_exc = exc
                logger.warning(
                    f"RSS HTTP connect failed attempt={attempt}/{attempts} url={url}: {exc}"
                )
                if attempt < attempts:
                    await asyncio.sleep(1.5 * attempt)
                continue
            raise
    assert last_exc is not None
    raise last_exc


_OG_DESC_RE = re.compile(
    r"""<meta[^>]+(?:property|name)=["'](?:og:description|description)["'][^>]+content=["']([^"']+)["']""",
    re.IGNORECASE,
)
_OG_DESC_RE_ALT = re.compile(
    r"""<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["'](?:og:description|description)["']""",
    re.IGNORECASE,
)


def extract_og_description(html: str) -> str | None:
    for pattern in (_OG_DESC_RE, _OG_DESC_RE_ALT):
        match = pattern.search(html or "")
        if not match:
            continue
        text = re.sub(r"\s+", " ", match.group(1)).strip()
        if len(text) >= 20:
            return text[:1500]
    return None


async def resolve_article_image(item: RssNewsItem, *, timeout: float = 15.0) -> str | None:
    """Use feed image if present; else one GET of article page for og:image."""
    enriched = await enrich_news_item(item, timeout=timeout)
    return enriched.image_url


async def enrich_news_item(item: RssNewsItem, *, timeout: float = 15.0) -> RssNewsItem:
    """Fill image_url / summary from article page when missing."""
    need_image = not (item.image_url and _looks_like_image_url(item.image_url))
    need_summary = not (item.summary or "").strip()
    if not need_image and not need_summary:
        return item
    page = normalize_item_url(item.url) or (item.url or "").strip()
    if not page.startswith("http"):
        return item
    try:
        resp = await _http_get_with_retry(page, timeout=timeout)
        html = resp.content.decode("utf-8", errors="ignore")
        page_url = str(resp.url)
        if need_image:
            found = extract_og_image(html, page_url=page_url)
            if found:
                item.image_url = found
        if need_summary:
            desc = extract_og_description(html)
            if desc:
                item.summary = desc
    except Exception as e:
        logger.warning(f"Article enrich failed url={page}: {e}")
    return item


def parse_feed_bytes(content: bytes, feed_url: str) -> list[RssNewsItem]:
    parsed = feedparser.parse(content)
    items: list[RssNewsItem] = []
    for entry in parsed.entries or []:
        title = (getattr(entry, "title", None) or "").strip()
        link = normalize_item_url(
            (getattr(entry, "link", None) or "").strip()
        )
        raw_guid = (
            getattr(entry, "id", None) or getattr(entry, "guid", None) or link or title
        )
        guid = str(raw_guid).strip()
        if link and (not guid or guid == title):
            guid = link
        elif link:
            # keep feed guid but prefer normalized link for url field
            pass
        if not guid:
            continue
        raw_summary = getattr(entry, "summary", None) or getattr(entry, "description", None) or ""
        raw_summary = raw_summary.strip() if hasattr(raw_summary, "strip") else str(raw_summary)
        image_url = extract_entry_image_url(entry, page_url=link or feed_url)
        summary = raw_summary
        if "<" in summary:
            summary = re.sub(r"<[^>]+>", " ", summary)
            summary = re.sub(r"\s+", " ", summary).strip()
        items.append(
            RssNewsItem(
                feed_url=feed_url,
                guid=guid,
                url=link,
                title=title or guid,
                summary=summary,
                published_at=_parse_entry_date(entry),
                image_url=image_url,
            )
        )
    return items


async def fetch_feed(
    url: str,
    *,
    timeout: float = 20.0,
    attempts: int = 3,
) -> list[RssNewsItem]:
    try:
        resp = await _http_get_with_retry(url, timeout=timeout, attempts=attempts)
        return parse_feed_bytes(resp.content, url)
    except Exception as e:
        logger.warning(f"RSS fetch failed url={url}: {e}")
        return []


async def fetch_all_feeds(feeds: list[str]) -> list[RssNewsItem]:
    items: list[RssNewsItem] = []
    for url in feeds:
        items.extend(await fetch_feed(url))
    return items


_FEED_LINK_RE = re.compile(
    r"""<link\b[^>]*\brel=["'][^"']*alternate[^"']*["'][^>]*>""",
    re.IGNORECASE,
)
_FEED_TYPE_RE = re.compile(
    r"""type=["'](application/(?:rss|atom)\+xml|text/xml)["']""",
    re.IGNORECASE,
)
_FEED_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)
_COMMON_FEED_PATHS = ("/rss", "/feed", "/atom.xml", "/rss.xml", "/feeds/posts/default")
_LISTING_FALLBACK_PATHS = ("/news/", "/novosti/", "/news", "/novosti")
_ARTICLE_PATH_HINTS = (
    "/news/",
    "/novosti/",
    "/new/",
    "/article/",
    "/articles/",
    "/post/",
    "/posts/",
    "/story/",
    "/stories/",
    "/publication/",
    "/material/",
    "/materials/",
)
_SKIP_PATH_HINTS = (
    "/tag/",
    "/tags/",
    "/category/",
    "/categories/",
    "/login",
    "/signup",
    "/register",
    "/about",
    "/contact",
    "/search",
    "/cart",
    "/account",
    "/rss",
    "/feed",
    "/atom",
    "/feeds/",
)
_DATE_IN_PATH_RE = re.compile(r"/(?:19|20)\d{2}/(?:0?[1-9]|1[0-2])/")
_FULL_DATE_IN_PATH_RE = re.compile(
    r"/(?P<year>(?:19|20)\d{2})/(?P<month>0?[1-9]|1[0-2])/(?P<day>0?[1-9]|[12]\d|3[01])(?:/|$)"
)
_NUMERIC_ARTICLE_RE = re.compile(
    r"/(?:news|novosti|new|article|articles|post|posts|story|material)s?/(\d{3,})(?:/|$)",
    re.IGNORECASE,
)
_LONG_NUMERIC_ID_RE = re.compile(r"/\d{5,}(?:/|$)")


def infer_published_at_from_url(url: str) -> datetime | None:
    """Parse YYYY/MM/DD from article path (e.g. lenta.ru/news/2022/08/02/...)."""
    raw = (url or "").strip()
    if not raw:
        return None
    path = urlparse(raw).path or raw
    match = _FULL_DATE_IN_PATH_RE.search(path)
    if not match:
        return None
    try:
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


@dataclass
class SiteAddResult:
    mode: str  # "feed" | "site"
    stored_url: str
    item_count: int
    message: str


def discover_feed_urls(page_url: str, html: str) -> list[str]:
    """Extract RSS/Atom alternate link hrefs from HTML head markup."""
    found: list[str] = []
    for tag in _FEED_LINK_RE.findall(html or ""):
        href_m = _FEED_HREF_RE.search(tag)
        if not href_m:
            continue
        href = href_m.group(1).strip()
        if not href:
            continue
        type_ok = bool(_FEED_TYPE_RE.search(tag))
        low = href.casefold()
        href_ok = any(x in low for x in ("rss", "atom", "feed"))
        if not (type_ok or href_ok):
            continue
        abs_url = urljoin(page_url, href)
        if abs_url not in found:
            found.append(abs_url)
    return found


def parse_html_listing(html: str, site_url: str, *, limit: int = 40) -> list[RssNewsItem]:
    """Generic same-host article link extractor from a listing page."""
    from html.parser import HTMLParser

    class _AnchorParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.links: list[tuple[str, str]] = []
            self._href: str | None = None
            self._parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag != "a":
                return
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._parts = []

        def handle_endtag(self, tag: str) -> None:
            if tag != "a" or self._href is None:
                return
            text = re.sub(r"\s+", " ", "".join(self._parts)).strip()
            self.links.append((self._href, text))
            self._href = None
            self._parts = []

        def handle_data(self, data: str) -> None:
            if self._href is not None:
                self._parts.append(data)

    parser = _AnchorParser()
    try:
        parser.feed(html or "")
    except Exception:
        return []

    base_host = (urlparse(site_url).netloc or "").casefold()
    items: list[RssNewsItem] = []
    seen: set[str] = set()
    for href, text in parser.links:
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        abs_url = urljoin(site_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        if (parsed.netloc or "").casefold() != base_host:
            continue
        path = parsed.path or "/"
        path_l = path.casefold()
        if any(s in path_l for s in _SKIP_PATH_HINTS):
            continue
        # Drop bare section roots like /news/
        if path.rstrip("/") in (
            "",
            "/news",
            "/novosti",
            "/articles",
            "/posts",
            "/new",
            "/materials",
        ):
            continue
        hint_ok = (
            any(h in path_l for h in _ARTICLE_PATH_HINTS)
            or bool(_DATE_IN_PATH_RE.search(path))
            or bool(_NUMERIC_ARTICLE_RE.search(path))
            or bool(_LONG_NUMERIC_ID_RE.search(path))
        )
        text_ok = len(text) >= 18
        if not (hint_ok or text_ok):
            continue
        # Prefer paths with some depth
        if path.count("/") < 2 and not hint_ok:
            continue
        clean = normalize_item_url(abs_url)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        title = text if len(text) >= 8 else clean.rstrip("/").rsplit("/", 1)[-1]
        items.append(
            RssNewsItem(
                feed_url=site_url,
                guid=clean,
                url=clean,
                title=title[:300],
                summary="",
                published_at=None,
            )
        )
        if len(items) >= limit:
            break
    return items


async def _fetch_page_bytes(
    url: str,
    *,
    timeout: float = 20.0,
    attempts: int = 3,
) -> tuple[bytes, str]:
    resp = await _http_get_with_retry(url, timeout=timeout, attempts=attempts)
    return resp.content, str(resp.url)


async def _try_fetch_page(
    url: str,
    *,
    timeout: float = 20.0,
    attempts: int = 3,
) -> tuple[bytes, str] | None:
    try:
        return await _fetch_page_bytes(url, timeout=timeout, attempts=attempts)
    except Exception as e:
        logger.warning(f"Site fetch failed url={url}: {e}")
        return None


def _site_origin(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    return f"{scheme}://{netloc}" if netloc else url


async def probe_common_feed_paths(base_url: str) -> list[str]:
    """Try well-known feed paths under the site origin (single attempt each)."""
    origin = _site_origin(base_url)
    working: list[str] = []
    for path in _COMMON_FEED_PATHS:
        candidate = origin + path
        items = await fetch_feed(candidate, attempts=1)
        if items:
            working.append(candidate)
    return working


async def find_working_feed_for_site(site_url: str) -> tuple[str | None, list[RssNewsItem], bytes]:
    """Return (feed_url, items, page_html_bytes) — feed_url None if only HTML remains."""
    content = b""
    final_url = site_url

    primary = await _try_fetch_page(site_url, attempts=3)
    if primary:
        content, final_url = primary
        direct = parse_feed_bytes(content, final_url)
        if direct:
            return final_url, direct, content
    else:
        origin = _site_origin(site_url)
        for path in _LISTING_FALLBACK_PATHS:
            candidate = origin + path
            if normalize_item_url(candidate) == normalize_item_url(site_url):
                continue
            alt = await _try_fetch_page(candidate, attempts=1)
            if alt:
                content, final_url = alt
                direct = parse_feed_bytes(content, final_url)
                if direct:
                    return final_url, direct, content
                break

    html = ""
    if content:
        try:
            html = content.decode("utf-8", errors="ignore")
        except Exception:
            html = ""

    candidates: list[str] = []
    if html:
        candidates.extend(discover_feed_urls(final_url, html))
    for path_feed in await probe_common_feed_paths(final_url or site_url):
        if path_feed not in candidates:
            candidates.append(path_feed)

    for feed_url in candidates:
        items = await fetch_feed(feed_url, attempts=1)
        if items:
            return feed_url, items, content

    return None, [], content


async def _listing_from_html_or_fallbacks(
    site_url: str,
    content: bytes,
    *,
    final_url: str = "",
) -> list[RssNewsItem]:
    html = ""
    try:
        html = content.decode("utf-8", errors="ignore") if content else ""
    except Exception:
        html = ""
    base = final_url or site_url
    if html:
        listing = parse_html_listing(html, base)
        if listing:
            return listing

    origin = _site_origin(site_url)
    tried = {normalize_item_url(base), normalize_item_url(site_url)}
    for path in _LISTING_FALLBACK_PATHS:
        candidate = origin + path
        if normalize_item_url(candidate) in tried:
            continue
        tried.add(normalize_item_url(candidate))
        got = await _try_fetch_page(candidate, attempts=1)
        if not got:
            continue
        page_bytes, page_url = got
        try:
            page_html = page_bytes.decode("utf-8", errors="ignore")
        except Exception:
            continue
        listing = parse_html_listing(page_html, page_url)
        if listing:
            return listing
    return []


async def resolve_site_to_items(site_url: str) -> list[RssNewsItem]:
    feed_url, feed_items, content = await find_working_feed_for_site(site_url)
    if feed_url and feed_items:
        return feed_items
    return await _listing_from_html_or_fallbacks(site_url, content)


async def fetch_all_sites(sites: list[str]) -> list[RssNewsItem]:
    items: list[RssNewsItem] = []
    for url in sites:
        if _is_vk_url(url):
            logger.warning(f"Skipping unsupported VK site url={url}")
            continue
        try:
            items.extend(await resolve_site_to_items(url))
        except Exception as e:
            logger.warning(f"Site resolve failed url={url}: {e}")
    return items


async def resolve_site_add(site_url: str) -> SiteAddResult:
    """On user add: prefer RSS feed URL in feeds; else keep as HTML site source."""
    if _is_vk_url(site_url):
        return SiteAddResult(
            mode="site",
            stored_url=site_url,
            item_count=0,
            message=(
                "VK-сообщества пока не поддерживаются как источник. "
                "Добавь сайт СМИ или прямую RSS-ссылку."
            ),
        )
    feed_url, feed_items, content = await find_working_feed_for_site(site_url)
    if feed_url and feed_items:
        return SiteAddResult(
            mode="feed",
            stored_url=feed_url,
            item_count=len(feed_items),
            message=(
                f"Нашёл RSS, добавил ленту ({len(feed_items)} записей):\n`{feed_url}`"
            ),
        )
    listing = await _listing_from_html_or_fallbacks(site_url, content)
    if listing:
        return SiteAddResult(
            mode="site",
            stored_url=site_url,
            item_count=len(listing),
            message=(
                f"RSS нет — буду читать список со страницы "
                f"(сейчас вижу ~{len(listing)} ссылок)."
            ),
        )
    return SiteAddResult(
        mode="site",
        stored_url=site_url,
        item_count=0,
        message=(
            "RSS не нашёл и список новостей пока пустой. "
            "Ссылку сохранил — попробуем при опросе ещё раз."
        ),
    )


def _matches_any(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return False
    for kw in keywords:
        needle = kw.casefold().strip()
        if needle and needle in text:
            return True
    return False


def filter_new_items(
    items: list[RssNewsItem],
    *,
    seen_guids: set[str],
    seen_urls: set[str],
    max_age_hours: int,
    include_keywords: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
    seen_titles: set[str] | None = None,
    now: datetime | None = None,
) -> list[RssNewsItem]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=max_age_hours)
    include = list(include_keywords or [])
    exclude = list(exclude_keywords or [])
    title_seen = set(seen_titles or [])
    out: list[RssNewsItem] = []
    local_guids: set[str] = set()
    local_urls: set[str] = set()
    local_titles: set[str] = set()
    for item in items:
        norm_url = normalize_item_url(item.url) if item.url else ""
        if norm_url:
            item.url = norm_url
            if item.guid == item.url or not item.guid:
                item.guid = norm_url
        guid = item.guid
        if guid in seen_guids or guid in local_guids:
            continue
        if norm_url and (norm_url in seen_urls or norm_url in local_urls):
            continue
        if item.url and (item.url in seen_urls or item.url in local_urls):
            continue
        tkey = title_dedup_key(item.title)
        if tkey and (tkey in title_seen or tkey in local_titles):
            continue
        if item.published_at is None:
            inferred = infer_published_at_from_url(norm_url or item.url or "")
            if inferred is not None:
                item.published_at = inferred
        if item.published_at is None:
            continue
        if item.published_at < cutoff:
            continue
        hay = item.haystack()
        if exclude and _matches_any(hay, exclude):
            continue
        if include and not _matches_any(hay, include):
            continue
        out.append(item)
        local_guids.add(guid)
        if norm_url:
            local_urls.add(norm_url)
        if item.url:
            local_urls.add(item.url)
        if tkey:
            local_titles.add(tkey)
    out.sort(
        key=lambda x: x.published_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return out


def pick_next(items: list[RssNewsItem]) -> RssNewsItem | None:
    return items[0] if items else None


def _seen_row_from_item(
    it: RssNewsItem,
    *,
    channel_id: int,
    pipeline_run_id: int | None,
) -> RssSeenItem:
    norm_url = normalize_item_url(it.url) if it.url else (it.url or "")
    guid = it.guid
    if norm_url and (not guid or guid == it.url):
        guid = norm_url
    return RssSeenItem(
        channel_id=channel_id,
        pipeline_run_id=pipeline_run_id,
        feed_url=it.feed_url,
        item_guid=guid,
        item_url=norm_url,
        title=it.title,
        published_at=it.published_at,
        processed_at=datetime.now(UTC),
    )


async def baseline_mark(
    repo: SQLARssSeenRepository,
    *,
    channel_id: int,
    feeds: list[str],
    sites: list[str] | None = None,
) -> int:
    items = await fetch_all_feeds(feeds)
    items.extend(await fetch_all_sites(list(sites or [])))
    seen_rows = [
        _seen_row_from_item(it, channel_id=channel_id, pipeline_run_id=None)
        for it in items
    ]
    count = await repo.mark_many(seen_rows)
    logger.info(
        f"RSS baseline channel_id={channel_id} feeds={len(feeds)} "
        f"sites={len(sites or [])} items={len(items)} inserted={count}"
    )
    return count


async def collect_new_for_channel(
    repo: SQLARssSeenRepository,
    *,
    channel_id: int,
    news_cfg: dict[str, Any],
    now: datetime | None = None,
) -> list[RssNewsItem]:
    now = now or datetime.now(UTC)
    cfg = normalize_news_rss(news_cfg)
    feeds = list(cfg["feeds"])
    sites = list(cfg["sites"])
    if not feeds and not sites:
        return []
    raw_items = await fetch_all_feeds(feeds)
    raw_items.extend(await fetch_all_sites(sites))
    guids, urls, titles = await repo.get_seen_keys(channel_id)
    seen_urls: set[str] = set()
    for u in urls:
        if not u:
            continue
        seen_urls.add(u)
        seen_urls.add(normalize_item_url(u))
    seen_titles = {title_dedup_key(t) for t in titles if title_dedup_key(t)}
    return filter_new_items(
        raw_items,
        seen_guids=guids,
        seen_urls=seen_urls,
        seen_titles=seen_titles,
        max_age_hours=int(cfg["max_age_hours"]),
        include_keywords=list(cfg["include_keywords"]),
        exclude_keywords=list(cfg["exclude_keywords"]),
        now=now,
    )


async def rate_limit_allows(
    repo: SQLARssSeenRepository,
    *,
    channel_id: int,
    max_posts_per_hour: int,
    now: datetime | None = None,
) -> bool:
    if max_posts_per_hour <= 0:
        return True
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=1)
    count = await repo.count_published_since(channel_id, since)
    return count < max_posts_per_hour


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


async def generate_keywords_for_topic(
    openai_client: Any,
    *,
    niche: str,
    topic_brief: str = "",
    channel_title: str = "",
    channel_topic: str = "",
) -> dict[str, Any]:
    """Ask OpenAI for include/exclude keywords. Falls back to niche preset."""
    niche_label = NICHE_LABELS.get(niche, niche or "общая тема")
    brief = (topic_brief or "").strip() or niche_label
    system = (
        "Ты помощник редактора новостного канала. "
        "Подбери ключевые слова для фильтрации RSS. "
        "Ответь ТОЛЬКО валидным JSON без markdown."
    )
    user = (
        f"Канал: {channel_title or '—'}\n"
        f"Тематика канала: {channel_topic or '—'}\n"
        f"Ниша/тема фильтра: {brief}\n\n"
        f"Верни JSON вида:\n"
        f'{{"include": ["слово1", "..."], "exclude": ["spam1", "..."], '
        f'"reason": "кратко почему"}}\n\n'
        f"Правила:\n"
        f"- include: 8-15 слов/фраз (RU+EN), по которым новость ПРОХОДИТ\n"
        f"- exclude: 5-10 слов спама/мусора\n"
        f"- для широкой «общей» темы include может быть []\n"
        f"- не используй слишком общие слова вроде «новости», «сегодня»\n"
    )
    try:
        raw = await openai_client.generate_text(prompt=user, system_prompt=system)
        parsed = _extract_json_object(raw)
        if parsed:
            include = _clean_keyword_list(parsed.get("include"))
            exclude = _clean_keyword_list(parsed.get("exclude"))
            reason = str(parsed.get("reason") or "").strip()[:300]
            if include or exclude or niche == "general":
                return {
                    "include": include,
                    "exclude": exclude,
                    "reason": reason or "Подобрано ИИ под тему канала.",
                    "source": "ai",
                }
    except Exception as e:
        logger.warning(f"AI keyword generation failed: {e}")

    preset = preset_keywords(niche if niche in NICHE_PRESETS else "general")
    return {
        "include": preset["include"],
        "exclude": preset["exclude"],
        "reason": "Использован готовый пресет (ИИ недоступен или ответ невалиден).",
        "source": "preset",
    }


def format_keywords_review(
    *,
    niche: str,
    include: list[str],
    exclude: list[str],
    reason: str = "",
) -> str:
    label = NICHE_LABELS.get(niche, niche or "тема")
    lines = [
        f"📰 *Фильтр RSS — {label}*",
        "",
    ]
    if reason:
        lines.append(reason)
        lines.append("")
    inc = ", ".join(include) if include else "— (все, кроме исключений)"
    exc = ", ".join(exclude) if exclude else "—"
    lines.append(f"*Включать (+):*\n{inc}")
    lines.append("")
    lines.append(f"*Исключать (−):*\n{exc}")
    lines.append("")
    lines.append("Утверди набор или переделай.")
    return "\n".join(lines)

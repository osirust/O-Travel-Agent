"""Optional city photos from Wikimedia; unrelated to hotel and price data."""
from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

import httpx

CITY_PAGES = {"Москва": "Moscow", "Санкт-Петербург": "Saint Petersburg", "Сочи": "Sochi", "Казань": "Kazan", "Стамбул": "Istanbul", "Анталья": "Antalya", "Дубай": "Dubai"}
USER_AGENT = "OTravelHackathon/1.0 (https://github.com/ostrovok-hackathon-2026/aegml)"


def _text(value: object, length: int = 180) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"<[^>]+>", "", html.unescape(value)).split())[:length]


def _safe_url(value: object, host: str, prefix: str) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 or c == "\\" for c in value):
        return None
    try:
        url = urlsplit(value)
        allowed_query = host == "thumb.wikimedia.org" and parse_qsl(url.query, keep_blank_values=True) == [("utm_source", "commons.wikimedia.org"), ("utm_campaign", "imageinfo"), ("utm_content", "thumbnail")]
        if url.scheme != "https" or url.hostname != host or url.username is not None or url.password is not None or url.port not in {None, 443} or (url.query and not allowed_query) or url.fragment or not url.path.startswith(prefix):
            return None
    except ValueError:
        return None
    return value


class DestinationPhotos:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False, headers={"User-Agent": USER_AGENT})
        self.cache: dict[str, tuple[datetime, dict | None]] = {}
        self.lock = asyncio.Lock()

    async def close(self):
        await self.client.aclose()

    async def resolve(self, cities: list[str]) -> dict[str, dict]:
        selected = list(dict.fromkeys(city for city in cities if city in CITY_PAGES))[:7]
        if not selected:
            return {}
        async with self.lock:
            now = datetime.now(timezone.utc)
            missing = [city for city in selected if city not in self.cache or self.cache[city][0] <= now]
            if missing:
                found: dict[str, dict] = {}
                try:
                    response = await self.client.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "format": "json", "formatversion": 2, "prop": "pageimages", "piprop": "name", "pilicense": "free", "titles": "|".join(CITY_PAGES[city] for city in missing)})
                    response.raise_for_status()
                    pages = response.json().get("query", {}).get("pages", [])
                    titles = {page.get("title"): page.get("pageimage") for page in pages if isinstance(page, dict) and isinstance(page.get("pageimage"), str)}
                    names = [titles[CITY_PAGES[city]] for city in missing if CITY_PAGES[city] in titles]
                    if names:
                        details = await self.client.get("https://commons.wikimedia.org/w/api.php", params={"action": "query", "format": "json", "formatversion": 2, "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 320, "titles": "|".join(f"File:{name}" for name in names)})
                        details.raise_for_status()
                        infos = {page.get("title", "").replace("_", " "): page.get("imageinfo", [None])[0] for page in details.json().get("query", {}).get("pages", []) if isinstance(page, dict) and page.get("imageinfo")}
                        for city in missing:
                            name = titles.get(CITY_PAGES[city])
                            info = infos.get(f"File:{name}".replace("_", " ")) or {}
                            meta = info.get("extmetadata") or {}
                            image = (_safe_url(info.get("thumburl"), "thumb.wikimedia.org", "/wikipedia/commons/thumb/")
                                     or _safe_url(info.get("thumburl"), "upload.wikimedia.org", "/wikipedia/commons/"))
                            page = _safe_url(info.get("descriptionurl"), "commons.wikimedia.org", "/wiki/File:")
                            author = _text((meta.get("Artist") or {}).get("value"))
                            license_name = _text((meta.get("LicenseShortName") or {}).get("value"), 80)
                            if image and page and author and license_name:
                                found[city] = {"url": image, "source_url": page, "author": author, "license": license_name, "source": "Wikimedia Commons"}
                except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
                    pass
                for city in missing:
                    item = found.get(city)
                    self.cache[city] = (now + timedelta(hours=12) if item else now + timedelta(minutes=5), item)
            return {city: self.cache[city][1] for city in selected if self.cache.get(city) and self.cache[city][1]}

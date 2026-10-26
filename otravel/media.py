"""Optional hotel-photo view data, separate from economic offer snapshots.

Only fixed LiteAPI sandbox JSON content is requested. Image URLs are returned
for the frontend, never fetched/proxied here. CDN provenance is not a claim of
copyright ownership, availability, a selected room, or a real booking.
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
from collections import OrderedDict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, unquote, urlsplit

from .providers import ProviderError, _safe_text

CONTENT_URL = "https://api.liteapi.travel/v3.0/data/hotel"
MAX_HOTELS = 5
MAX_PHOTOS = 5
MAX_OFFER_ITEMS = 32
MAX_CACHED_HOTELS = 32
POSITIVE_TTL_SECONDS = 6 * 60 * 60
NEGATIVE_TTL_SECONDS = 60
_CDN_PATHS = {
    "snaphotelapi.com": r"/hotels/[A-Za-z0-9/_-]+\.(?:jpe?g|png|webp|avif)",
    # Observed in an authenticated LiteAPI content response for lp1b30d1;
    # this is an exact CDN/path addition, not an open domain suffix policy.
    "static.cupid.travel": r"/hotels/[A-Za-z0-9/_-]+\.(?:jpe?g|png|webp|avif)",
    "liteapi-travel-static-data.s3.amazonaws.com": r"/images/hotels/[A-Za-z0-9/_-]+\.(?:jpe?g|png|webp|avif)",
}
MEDIA_CSP_SOURCES = tuple("https://" + host for host in _CDN_PATHS)
_RESIZE_QUERY = {"width", "height", "w", "h", "quality", "q", "fit", "crop", "format", "fm", "auto"}


def safe_media_url(value) -> str | None:
    """Exact documented CDNs only; no DNS, image download or URL rewriting."""
    if (not isinstance(value, str) or not 0 < len(value) <= 4096 or not value.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in value)):
        return None
    decoded = unquote(value)
    if any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in decoded):
        return None
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None
                or port not in {None, 443} or parsed.fragment):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        path_pattern = _CDN_PATHS.get(host)
        if path_pattern is None or re.fullmatch(path_pattern, parsed.path, flags=re.IGNORECASE) is None:
            return None
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=12)
        if any(key not in _RESIZE_QUERY or re.fullmatch(r"[A-Za-z0-9_.+,-]{0,80}", item) is None for key, item in query):
            return None
    except (ValueError, UnicodeError):
        return None
    return value


def _text(value, limit=200, secret=None) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"<[^>]*>", "", html.unescape(value))
    text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", _safe_text(text)).split())
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return text[:limit] or None


def _hotel_id(value) -> str | None:
    if (not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None
            or value.casefold().startswith("demo")):
        return None
    return value


def _placeholder(hotel_id=None) -> dict:
    return {"url": None, "thumbnailUrl": None, "alt": "Фотография отеля недоступна",
        "kind": "PLACEHOLDER", "source": "NONE", "hotelId": hotel_id, "sourceHotelId": None,
        "isActualPropertyPhoto": False, "caption": None, "gallery": []}


def _media(hotel_id: str, content: dict) -> dict:
    if not isinstance(content, dict) or content.get("id") != hotel_id:
        return _placeholder(hotel_id)
    name = _text(content.get("name"), 120)
    fallback_alt = "Фотография отеля" + (f" «{name}»" if name else "")
    rows = content.get("hotelImages")
    rows = rows[:100] if isinstance(rows, list) else []
    candidates = [row for row in rows if isinstance(row, dict)]
    candidates.sort(key=lambda row: (row.get("defaultImage") is not True,
        row["order"] if type(row.get("order")) is int and row["order"] >= 0 else 1_000_000))
    if content.get("main_photo"):
        candidates.append({"url": content["main_photo"], "thumbnailUrl": content.get("thumbnail")})
    photos, seen = [], set()
    thumbnail = None
    for row in candidates:
        url = safe_media_url(row.get("url"))
        if url is None or url in seen:
            continue
        seen.add(url)
        caption = _text(row.get("caption"))
        if not photos:
            thumbnail = safe_media_url(row.get("thumbnailUrl"))
            if thumbnail is None and url == content.get("main_photo"):
                thumbnail = safe_media_url(content.get("thumbnail"))
        photos.append({"url": url, "alt": caption or fallback_alt, "caption": caption})
        if len(photos) == MAX_PHOTOS:
            break
    if not photos:
        return _placeholder(hotel_id)
    return {"url": photos[0]["url"], "thumbnailUrl": thumbnail, "alt": photos[0]["alt"],
        "kind": "HOTEL", "source": "LITEAPI", "hotelId": hotel_id, "sourceHotelId": content["id"],
        "isActualPropertyPhoto": True, "caption": photos[0]["caption"], "gallery": photos}


class MediaResolver:
    def __init__(self, gateway):
        self.gateway = gateway
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._lock = asyncio.Lock()

    def _now(self) -> datetime:
        now = self.gateway._now()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ProviderError("INVALID_CLOCK", "Для медиа нужно время с часовым поясом.", provider="liteapi")
        return now.astimezone(UTC)

    def _remember(self, hotel_id, entry):
        self._cache[hotel_id] = entry
        self._cache.move_to_end(hotel_id)
        while len(self._cache) > MAX_CACHED_HOTELS:
            self._cache.popitem(last=False)

    async def hotel_content(self, hotel_id: str) -> dict:
        """One fixed JSON lookup with positive/negative private process cache."""
        if _hotel_id(hotel_id) is None:
            raise ProviderError("INVALID_REQUEST", "Нужен точный ID гостиницы поставщика, без DEMO-подстановки.", provider="liteapi")
        settings = self.gateway.settings
        mode = settings.get("LITEAPI_MODE", settings.get("liteapi_mode", "sandbox"))
        key = self.gateway._key("LITEAPI_API_KEY", "liteapi")
        if str(mode).lower() != "sandbox" or not key.startswith(("sand_", "sandbox_")):
            raise ProviderError("CONFIG_BLOCKED", "Для медиа разрешён только LiteAPI sandbox.", provider="liteapi")
        async with self._lock:
            now = self._now()
            saved = self._cache.get(hotel_id)
            if saved and saved["retrieved_at"] <= now < saved["expires_at"]:
                self._cache.move_to_end(hotel_id)
                if saved.get("error_code"):
                    raise ProviderError("MEDIA_NEGATIVE_CACHE", "Медиа временно недоступны; повторный запрос отложен.",
                        True, "liteapi", {"cause_code": saved["error_code"]})
                return deepcopy(saved["content"])
            try:
                body = await asyncio.wait_for(self.gateway._request("liteapi", "GET", CONTENT_URL,
                    headers={"X-API-Key": key}, params={"hotelId": hotel_id, "timeout": 4}), timeout=15)
                data = body.get("data") if isinstance(body, dict) else None
                if not isinstance(data, dict) or data.get("id") != hotel_id:
                    raise ProviderError("MEDIA_IDENTITY_MISMATCH", "Контент не подтверждает запрошенный ID отеля.", provider="liteapi")
                rows = data.get("hotelImages")
                content = {"id": hotel_id, "name": _text(data.get("name"), 120, key), "hotelImages": []}
                for row in (rows[:100] if isinstance(rows, list) else []):
                    if not isinstance(row, dict):
                        continue
                    normalized = {"caption": _text(row.get("caption"), secret=key),
                        "defaultImage": row.get("defaultImage") is True,
                        "order": row.get("order") if type(row.get("order")) is int else None}
                    for field in ("url", "thumbnailUrl"):
                        value = row.get(field)
                        normalized[field] = safe_media_url(value) if isinstance(value, str) and key not in unquote(value) else None
                    content["hotelImages"].append(normalized)
                for field in ("main_photo", "thumbnail"):
                    value = data.get(field)
                    content[field] = safe_media_url(value) if isinstance(value, str) and key not in unquote(value) else None
            except (ProviderError, TimeoutError) as error:
                code = error.code if isinstance(error, ProviderError) else "REQUEST_TIMEOUT"
                self._remember(hotel_id, {"retrieved_at": now, "expires_at": now + timedelta(seconds=NEGATIVE_TTL_SECONDS), "error_code": code})
                if isinstance(error, ProviderError):
                    raise
                raise ProviderError("REQUEST_TIMEOUT", "Фото-контент не получен вовремя; автоматического повтора нет.", True, "liteapi") from None
            ttl = POSITIVE_TTL_SECONDS if _media(hotel_id, content)["isActualPropertyPhoto"] else NEGATIVE_TTL_SECONDS
            self._remember(hotel_id, {"retrieved_at": now, "expires_at": now + timedelta(seconds=ttl), "content": content})
            return deepcopy(content)

    async def hotel_media(self, hotel_id: str) -> dict:
        try:
            return _media(hotel_id, await self.hotel_content(hotel_id))
        except ProviderError:
            return _placeholder(_hotel_id(hotel_id))

    async def resolve_plan(self, plan: dict) -> dict:
        """At most5 unique hotel IDs; errors affect only optional media view data."""
        result = {"items": [], "warnings": []}
        offers = plan.get("offers") if isinstance(plan, dict) else None
        if not isinstance(offers, list):
            result["warnings"].append({"code": "MEDIA_PLAN_INVALID", "message": "Нет списка предложений для фото."})
            return result
        seen = set()
        if len(offers) > MAX_OFFER_ITEMS:
            result["warnings"].append({"code": "MEDIA_OFFER_LIMIT", "message": "Фото ограничены первыми32 компонентами."})
        for offer in offers[:MAX_OFFER_ITEMS]:
            if not isinstance(offer, dict):
                continue
            details = offer.get("details") if isinstance(offer.get("details"), dict) else {}
            if offer.get("kind", details.get("kind")) != "hotel" or not isinstance(offer.get("offer_id"), str):
                continue
            binding = details.get("binding") if isinstance(details.get("binding"), dict) else {}
            ids = [value for value in (binding.get("hotel_id"), offer.get("hotel_id"), details.get("hotel_id")) if value is not None]
            hotel_id = ids[0] if ids and all(value == ids[0] for value in ids) else None
            hotel_id = _hotel_id(hotel_id)
            media = _placeholder(hotel_id)
            item = {"offer_id": offer["offer_id"], "component_id": offer.get("component_id", binding.get("stay_id")), "media": media}
            result["items"].append(item)
            code = None
            if hotel_id is None:
                code = "MEDIA_HOTEL_ID_UNKNOWN"
            elif (offer.get("source_type") not in {"SANDBOX", "LIVE_INDICATIVE", "LIVE_BOOKABLE"}
                    or str(offer.get("provider", "")).lower() != "liteapi"):
                code = "MEDIA_PROVIDER_UNSUPPORTED"
            elif hotel_id not in seen and len(seen) >= MAX_HOTELS:
                code = "MEDIA_HOTEL_LIMIT"
            else:
                seen.add(hotel_id)
                try:
                    item["media"] = _media(hotel_id, await self.hotel_content(hotel_id))
                    if not item["media"]["isActualPropertyPhoto"]:
                        code = "MEDIA_NO_SAFE_PHOTOS"
                except ProviderError as error:
                    code = error.code
            if code:
                result["warnings"].append({"code": code, "offer_id": offer["offer_id"], "message": "Фото недоступно; цена и выбор предложения не изменены."})
        return result

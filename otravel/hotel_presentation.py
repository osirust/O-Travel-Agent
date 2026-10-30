"""Bounded presentation data for hotel search results, never a priced offer."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


def google_hotel_photo(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or not value.isascii():
        return None
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.hostname not in {"lh3.googleusercontent.com", "lh4.googleusercontent.com", "lh5.googleusercontent.com", "lh6.googleusercontent.com"}
                or url.username is not None or url.password is not None or url.port not in {None, 443}
                or url.query or url.fragment or not re.fullmatch(r"/[A-Za-z0-9_./=-]+", url.path)):
            return None
    except ValueError:
        return None
    return value


def google_hotel_presentation(row: dict) -> dict:
    photos = []
    images = row.get("images")
    for item in images[:30] if isinstance(images, list) else []:
        if not isinstance(item, dict):
            continue
        # Use Google's search thumbnail only: arbitrary provider image hosts are not trusted.
        url = google_hotel_photo(item.get("thumbnail"))
        if url and url not in photos:
            photos.append(url)
        if len(photos) == 5:
            break
    rating = row.get("overall_rating")
    count = row.get("reviews")
    return {"photos": photos,
            "rating": rating if type(rating) in {int, float} and 0 <= rating <= 5 else None,
            "review_count": count if type(count) is int and 0 <= count <= 100_000_000 else None}

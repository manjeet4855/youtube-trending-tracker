
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
HISTORY = DATA / "history.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def fetch_feed():
    url = os.environ.get("TREND_FEED_URL", "").strip()
    parsed = urlparse(url)

    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(
            "Set TREND_FEED_URL to an HTTPS JSON feed you are "
            "authorized to use. No source is configured."
        )

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json",
                 "User-Agent": "PublicChartCollector/1.0"}
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(f"Feed returned HTTP {response.status}")
        raw = response.read(5_000_001)

    if len(raw) > 5_000_000:
        raise ValueError("Feed exceeds 5 MB limit.")

    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "videos", "trends", "results", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("Feed JSON must contain a video array.")


def first(item, *keys):
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return None


def as_int(value):
    if value is None:
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (ValueError, TypeError):
        return None


def normalize(item, index):
    if not isinstance(item, dict):
        return None

    video_id = first(item, "videoId", "video_id", "id")
    url = first(item, "url", "videoUrl", "video_url", "link")

    if not video_id and isinstance(url, str):
        if "youtu.be/" in url:
            video_id = url.split("youtu.be/", 1)[1].split("?", 1)[0]
        elif "watch?v=" in url:
            video_id = url.split("watch?v=", 1)[1].split("&", 1)[0]

    title = first(item, "title", "name", "videoTitle")
    if not title:
        return None

    views = as_int(first(
        item, "views", "viewCount", "view_count", "videoViews"
    ))
    rank = as_int(first(item, "rank", "position", "place")) or index + 1

    country = first(item, "country", "countryCode", "regionCode")
    category = first(item, "category", "chart", "type")

    if url and not str(url).startswith("https://"):
        url = None
    if not url and video_id:
        url = "https://www.youtube.com/watch?v=" + str(video_id)

    return {
        "id": str(video_id or url or title),
        "title": str(title),
        "channel": str(first(
            item, "channel", "channelTitle", "channel_name", "artist"
        ) or ""),
        "url": url,
        "thumbnail": first(item, "thumbnail", "thumbnailUrl") or "",
        "country": str(country or "GLOBAL").upper(),
        "category": str(category or "trending").lower(),
        "rank": rank,
        "views": views,
        "publishedAt": first(item, "publishedAt", "published_at") or ""
    }


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    old = read_json(LATEST, {})
    old_items = old.get("items", []) if isinstance(old, dict) else []
    old_by_key = {
        (str(x.get("country", "GLOBAL")).upper(),
         str(x.get("category", "trending")).lower(),
         str(x.get("id", ""))): x
        for x in old_items
    }

    incoming = fetch_feed()
    items = []
    seen = set()

    for index, raw in enumerate(incoming):
        item = normalize(raw, index)
        if not item:
            continue

        key = (item["country"], item["category"], item["id"])
        if key in seen:
            continue
        seen.add(key)

        previous = old_by_key.get(key)
        item["viewChange"] = None
        item["previousRank"] = None
        item["rankChange"] = None
        item["firstSeenInThisSnapshot"] = previous is None

        if previous:
            if item["views"] is not None and previous.get("views") is not None:
                item["viewChange"] = item["views"] - previous["views"]
            if previous.get("rank") is not None:
                item["previousRank"] = previous["rank"]
                item["rankChange"] = previous["rank"] - item["rank"]

        items.append(item)

    if not items:
        raise ValueError("Feed contained no usable video records.")

    timestamp = now()
    latest = {
        "updatedAt": timestamp,
        "source": "Configured public HTTPS JSON feed",
        "count": len(items),
        "items": items
    }

    history = read_json(HISTORY, {"snapshots": []})
    snapshots = history.get("snapshots", [])
    snapshots.append({
        "timestamp": timestamp,
        "items": [
            {
                "id": x["id"],
                "country": x["country"],
                "category": x["category"],
                "rank": x["rank"],
                "views": x["views"]
            }
            for x in items
        ]
    })

    # Keep the last 30 days of hourly snapshots.
    snapshots = snapshots[-720:]

    LATEST.write_text(
        json.dumps(latest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    HISTORY.write_text(
        json.dumps({"snapshots": snapshots},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Updated {len(items)} records at {timestamp}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Collection failed: {exc}", file=sys.stderr)
        sys.exit(1)

"""RSS fetching and parsing for News Arcade (the only network code in the plugin)."""

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import requests

USER_AGENT = "LEDMatrix-NewsArcade/1.0"
MAX_BYTES = 2 * 1024 * 1024

DEFAULT_FEEDS = [{"name": "TOP SPORTS", "url": "https://www.espn.com/espn/rss/news"}]

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean(text: Optional[str]) -> str:
    """Unescape, strip markup, collapse whitespace."""
    if not text:
        return ""
    text = html.unescape(_TAG.sub(" ", html.unescape(text)))
    text = _WS.sub(" ", text).strip()
    return re.sub(r"^\s*-\s*", "", text)


def parse_date(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text.strip())
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(node: ET.Element, *names: str) -> str:
    for child in node:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text
    return ""


def parse_feed(content: bytes, feed_name: str, limit: int) -> List[Dict[str, Any]]:
    """Headlines from RSS 2.0 (<item>) or Atom (<entry>)."""
    root = ET.fromstring(content)
    out: List[Dict[str, Any]] = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = clean(_child_text(node, "title"))
        if not title:
            continue
        link = _child_text(node, "link").strip()
        if not link:
            for child in node:
                if _local(child.tag) == "link" and child.get("href"):
                    link = child.get("href")
                    break
        out.append({
            "feed": feed_name,
            "title": title,
            "desc": clean(_child_text(node, "description", "summary")),
            "link": link,
            "ts": parse_date(_child_text(node, "pubDate", "published", "updated", "date")),
        })
        if len(out) >= limit:
            break
    return out


def age_text(ts: Optional[float], now: float) -> str:
    if not ts:
        return ""
    secs = max(0, now - ts)
    if secs < 90:
        return "NOW"
    if secs < 3600:
        return "%dM AGO" % (secs // 60)
    if secs < 86400:
        return "%dH AGO" % (secs // 3600)
    return "%dD AGO" % (secs // 86400)


def fetch(session: requests.Session, url: str, feed_name: str, limit: int, timeout: float) -> List[Dict[str, Any]]:
    resp = session.get(url, timeout=timeout, stream=True, headers={"User-Agent": USER_AGENT})
    try:
        resp.raise_for_status()
        body = resp.raw.read(MAX_BYTES + 1, decode_content=True)
    finally:
        resp.close()
    if len(body) > MAX_BYTES:
        raise ValueError("feed larger than %d bytes" % MAX_BYTES)
    return parse_feed(body, feed_name, limit)

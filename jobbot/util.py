"""Small helpers shared across the package."""

from __future__ import annotations

import hashlib
import html
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; jobbot/0.1; personal job search assistant)"


def html_to_text(raw: str | None) -> str:
    """Convert (possibly entity-escaped) HTML into readable plain text."""
    if not raw:
        return ""
    text = raw
    # Greenhouse returns HTML that is itself entity-escaped (&lt;p&gt;...).
    if "&lt;" in text and "<" not in text:
        text = html.unescape(text)
    if "<" not in text:
        return _squash(html.unescape(text))
    soup = BeautifulSoup(text, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for block in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr"]):
        block.insert_after("\n")
    return _squash(soup.get_text())


def _squash(text: str) -> str:
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation - for fuzzy comparisons and dedupe keys."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9+#]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode())
    return slug.strip("_")[:max_len].strip("_") or "x"


def short_hash(*parts: str, length: int = 12) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:length]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_date(value) -> datetime | None:
    """Parse ISO strings, epoch seconds/milliseconds and a few common formats into aware UTC datetimes."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        dt = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%d", "%d %b %Y", "%b %d, %Y"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, does not override variables already set."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

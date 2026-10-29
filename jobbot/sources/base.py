from __future__ import annotations

import logging
import time

import requests

from ..models import Job
from ..util import USER_AGENT

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    pass


class Source:
    """A job source. Subclasses implement `fetch()` and return Job objects."""

    name = "base"

    def __init__(self, cfg: dict, search: dict, session: requests.Session | None = None):
        self.cfg = cfg
        self.search = search
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def fetch(self) -> list[Job]:
        raise NotImplementedError

    def get_json(self, url: str, *, params: dict | None = None, headers: dict | None = None,
                 retries: int = 2, method: str = "GET", json_body: dict | None = None):
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(method, url, params=params, headers=headers, json=json_body, timeout=30)
            except requests.RequestException as exc:
                if attempt == retries:
                    raise SourceError(f"{self.name}: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == retries:
                    raise SourceError(f"{self.name}: HTTP {resp.status_code} for {url}")
                time.sleep(int(resp.headers.get("retry-after", 2 ** (attempt + 1))))
                continue
            if resp.status_code == 404:
                raise SourceError(f"{self.name}: not found (check the board/company name): {url}")
            if not resp.ok:
                raise SourceError(f"{self.name}: HTTP {resp.status_code} for {url}")
            return resp.json()
        raise SourceError(f"{self.name}: request failed: {url}")

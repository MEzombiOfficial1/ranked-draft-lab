import gzip
import http.client
import json
import os
import threading
import time
import urllib.parse

DEFAULT_BASE = "https://bsproxy.royaleapi.dev/v1"


class FatalApiError(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, rate, max_rate, min_rate=1.0):
        self.rate, self.max_rate, self.min_rate = float(rate), float(max_rate), min_rate
        self.lock = threading.Lock()
        self.next_slot = time.monotonic()
        self.paused_until = self.last_wait = self.last_cut = 0.0
        self.ceiling = None

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            slot = max(self.next_slot, now, self.paused_until)
            self.next_slot = slot + 1.0 / self.rate
            if slot > now:
                self.last_wait = now
        if slot > now:
            time.sleep(slot - now)

    def success(self):
        with self.lock:
            if time.monotonic() - self.last_wait > 1.0:
                return
            near_ceiling = self.ceiling is not None and self.rate >= 0.9 * self.ceiling
            self.rate = min(self.max_rate, self.rate + (0.1 if near_ceiling else 1.0) / self.rate)

    def throttled(self):
        with self.lock:
            now = time.monotonic()
            if now - self.last_cut >= 2.0:
                self.ceiling = self.rate
                self.rate = max(self.min_rate, self.rate * 0.8)
                self.last_cut = now
            self.paused_until = max(self.paused_until, now + 0.5)


class Api:
    def __init__(self, key, base=None, rate=10, max_rate=60, timeout=20):
        url = urllib.parse.urlsplit(base or os.environ.get("BS_API_BASE") or DEFAULT_BASE)
        self.conn_cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        self.host, self.prefix, self.timeout = url.netloc, url.path.rstrip("/"), timeout
        self.headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "ranked-collector/1.0",
        }
        self.limiter = RateLimiter(rate, max_rate)
        self.local = threading.local()
        self.lock = threading.Lock()
        self.stats = {"requests": 0, "ok": 0, "not_found": 0, "throttled": 0, "errors": 0}
        self.consecutive_failures = 0

    def _count(self, key, failure=None):
        with self.lock:
            self.stats[key] += 1
            if failure is True:
                self.consecutive_failures += 1
            elif failure is False:
                self.consecutive_failures = 0

    def _conn(self):
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = self.local.conn = self.conn_cls(self.host, timeout=self.timeout)
        return conn

    def _reset(self):
        conn = getattr(self.local, "conn", None)
        if conn is not None:
            conn.close()
        self.local.conn = None

    def get(self, path):
        status = None
        for attempt in range(5):
            self.limiter.acquire()
            self._count("requests")
            try:
                conn = self._conn()
                conn.request("GET", self.prefix + path, headers=self.headers)
                resp = conn.getresponse()
                body = resp.read()
                status = resp.status
                if resp.getheader("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            except (OSError, http.client.HTTPException):
                self._reset()
                self._count("errors", failure=True)
                time.sleep(min(8, 0.5 * 2 ** attempt))
                continue
            if status == 200:
                self.limiter.success()
                self._count("ok", failure=False)
                return status, json.loads(body)
            if status == 404:
                self._count("not_found", failure=False)
                return status, None
            if status == 429:
                self.limiter.throttled()
                self._count("throttled")
                time.sleep(1 + attempt)
                continue
            if status == 403:
                raise FatalApiError(f"HTTP 403 from API: {body[:300]!r} (check the key and its whitelisted IP)")
            self._count("errors", failure=True)
            if status >= 500:
                time.sleep(min(15, 2 ** attempt))
                continue
            return status, None
        return status, None

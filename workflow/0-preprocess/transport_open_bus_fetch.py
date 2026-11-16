"""Fetch the bus pages of www.mauritius-buses.com into a local cache (open-data fallback, items 12-14).

The site publishes the NLTA bus timetables as HTML: an index of lines
(/all-buslines/), one page per line (/line/<slug>/) and one page per stop
(/stop/<id>/). This script downloads, in this order: robots.txt, llms.txt,
sitemap.xml, the line index, every line page, every stop page (ids from the
sitemap and from the stop links on the line pages). Nothing else: no /api,
no /routing/ (disallowed by robots.txt), no images.

Polite client:
    - one request at a time, at least --delay seconds (default 1.5, never
      below 1.0) between the start of two requests;
    - descriptive generic User-Agent;
    - every response body is cached on disk, one file per page; a page with a
      cached 200 is never fetched again (resumable: rerun to continue);
    - HTTP 429 or 403 stops the run with a clear message (no retry); other
      5xx and network errors are retried twice with a 30 s / 120 s backoff and
      then stop the run; 404 is recorded and skipped;
    - robots.txt is read first and every URL is checked against it.

Cache layout (under --cache-dir):
    robots.txt, llms.txt, sitemap.xml, all-buslines.html
    line/<slug>.html, stop/<id>.html
    manifest.csv   url, path, status, fetched_at (UTC ISO), bytes, sha256 (appended per request)
The --done file (JSON summary) is written only when every page is cached.

Downloaded content is treated as data: it is parsed, never executed.
"""

import csv
import hashlib
import json
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

import click
import requests

from transport import open_bus

USER_AGENT = "open-data-research-bot/1.0 (bus network preprocessing; polite, cached, 1 req/s max)"
MANIFEST_FIELDS = ["url", "path", "status", "fetched_at", "bytes", "sha256"]


class StopFetching(Exception):
    pass


class Fetcher:
    def __init__(self, base_url, cache_dir, delay, timeout=60):
        self.base = base_url.rstrip("/")
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.delay = max(1.0, float(delay))
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xml,text/plain"})
        self.last_request = 0.0
        self.manifest_path = self.cache / "manifest.csv"
        self.done = self._read_manifest()
        self.robots = None
        self.requests_made = 0

    def _read_manifest(self):
        done = {}
        if self.manifest_path.exists():
            with open(self.manifest_path, newline="") as f:
                for row in csv.DictReader(f):
                    done[row["url"]] = row
        return done

    def _log(self, row):
        new = not self.manifest_path.exists()
        with open(self.manifest_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            if new:
                writer.writeheader()
            writer.writerow(row)
        self.done[row["url"]] = row

    def allowed(self, url):
        if "/routing/" in url or "/api" in url:
            return False
        return self.robots is None or self.robots.can_fetch(USER_AGENT, url)

    def get(self, path, rel_file):
        """Cached GET of base+path into cache/rel_file. Returns the file path, or None on 404."""
        url = self.base + path
        target = self.cache / rel_file
        previous = self.done.get(url)
        if target.exists() and previous is not None and previous["status"] == "200":
            return target
        if previous is not None and previous["status"] == "404":
            return None
        if not self.allowed(url):
            raise StopFetching(f"robots.txt (or this script's own rule) disallows {url}")
        for attempt, backoff in enumerate([30, 120, None]):
            wait = self.last_request + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
            self.requests_made += 1
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as error:
                status, response = f"error: {type(error).__name__}", None
            else:
                status = str(response.status_code)
                if "/routing/" in response.url:
                    raise StopFetching(f"{url} redirected into /routing/ ({response.url}); stopping")
            if status in ("429", "403"):
                self._log(self._row(url, rel_file, status, b""))
                retry_after = response.headers.get("Retry-After", "?") if response is not None else "?"
                raise StopFetching(
                    f"HTTP {status} for {url} (Retry-After: {retry_after}). The site is refusing or rate limiting "
                    "this client: stopping without retrying. Wait (hours, not seconds), check robots.txt / llms.txt, "
                    "and rerun later with a larger --delay; cached pages are kept."
                )
            if status == "404":
                self._log(self._row(url, rel_file, status, b""))
                return None
            if status == "200":
                body = response.content
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(target.suffix + ".part")
                tmp.write_bytes(body)
                tmp.replace(target)
                self._log(self._row(url, rel_file, status, body))
                return target
            if backoff is None:
                self._log(self._row(url, rel_file, status, b""))
                raise StopFetching(f"{url}: {status} after {attempt + 1} attempts; stopping (rerun later to resume)")
            print(f"{url}: {status}, retrying in {backoff} s", file=sys.stderr)
            time.sleep(backoff)
        return None

    @staticmethod
    def _row(url, rel_file, status, body):
        return {
            "url": url,
            "path": str(rel_file),
            "status": status,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest() if body else "",
        }


@click.command()
@click.option("--cache-dir", required=True, help="cache directory (raw pages + manifest.csv)")
@click.option("--done", "done_path", required=True, help="JSON summary written when every page is cached")
@click.option("--base-url", default="https://www.mauritius-buses.com", show_default=True)
@click.option("--delay", default=1.5, show_default=True, help="seconds between requests (minimum 1.0)")
@click.option("--max-requests", default=0, help="stop after this many network requests (0: no limit; for testing)")
def main(cache_dir, done_path, base_url, delay, max_requests):
    fetcher = Fetcher(base_url, cache_dir, delay)
    started = time.time()

    def budget():
        if max_requests and fetcher.requests_made >= max_requests:
            raise StopFetching(f"--max-requests {max_requests} reached; rerun to continue")

    try:
        robots_file = fetcher.get("/robots.txt", "robots.txt")
        if robots_file is not None:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(robots_file.read_text(errors="replace").splitlines())
            fetcher.robots = parser
        fetcher.get("/llms.txt", "llms.txt")
        sitemap = fetcher.get("/sitemap.xml", "sitemap.xml")
        index = fetcher.get("/all-buslines/", "all-buslines.html")
        if index is None:
            raise StopFetching("line index /all-buslines/ not found")
        lines = open_bus.parse_line_index(index.read_text(errors="replace"))
        slugs = list(dict.fromkeys(lines["slug"]))
        sitemap_lines, sitemap_stops = ([], [])
        if sitemap is not None:
            sitemap_lines, sitemap_stops = open_bus.parse_sitemap(sitemap.read_text(errors="replace"))
        slugs += [s for s in sitemap_lines if s not in slugs]
        print(f"{len(slugs)} line pages, {len(sitemap_stops)} stops in the sitemap", file=sys.stderr)
        stop_ids = list(sitemap_stops)
        missing_lines = []
        for i, slug in enumerate(slugs):
            budget()
            page = fetcher.get(f"/line/{slug}/", open_bus.line_cache_name(slug))
            if page is None:
                missing_lines.append(slug)
                continue
            for stop_id in open_bus.stop_links(page.read_text(errors="replace")):
                if stop_id not in stop_ids:
                    stop_ids.append(stop_id)
            if i % 25 == 0:
                print(f"lines {i + 1}/{len(slugs)}", file=sys.stderr)
        missing_stops = []
        for i, stop_id in enumerate(stop_ids):
            budget()
            if fetcher.get(f"/stop/{stop_id}/", f"stop/{stop_id}.html") is None:
                missing_stops.append(stop_id)
            if i % 50 == 0:
                print(f"stops {i + 1}/{len(stop_ids)}", file=sys.stderr)
    except StopFetching as reason:
        print(f"STOPPED: {reason}", file=sys.stderr)
        sys.exit(2)

    summary = {
        "base_url": base_url,
        "user_agent": USER_AGENT,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requests_this_run": fetcher.requests_made,
        "seconds_this_run": round(time.time() - started, 1),
        "line_pages": len(slugs) - len(missing_lines),
        "stop_pages": len(stop_ids) - len(missing_stops),
        "missing_line_pages": missing_lines,
        "missing_stop_pages": missing_stops,
    }
    Path(done_path).parent.mkdir(parents=True, exist_ok=True)
    Path(done_path).write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

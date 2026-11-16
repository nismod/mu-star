"""Download an open-data file (census workbook, OSM extract) once, and check it.

The file is written to a temporary name and moved into place only when it is
complete and has the expected format (by its leading bytes), so a failed or
truncated download never leaves a file that later rules would trust. A
``<output>.source.json`` sidecar records the URL, the time, the size and the
SHA-256. If the output already exists the download is skipped.

The downloaded files are data: they are never executed or imported.
"""

import datetime
import hashlib
import json
import os
import shutil
import ssl
import time
import urllib.request
from pathlib import Path

import click

#: Leading bytes of each expected format.
SIGNATURES = {
    "xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",  # OLE2 compound file (Excel 97-2003)
    "xlsx": b"PK\x03\x04",  # zip (Office Open XML)
    "pbf": b"\x00\x00",  # OSM PBF: 4-byte big-endian header length, then "OSMHeader"
    "pdf": b"%PDF",
}
USER_AGENT = "mu-star-open-data-download/1.0 (research workflow)"


def check_format(path: Path, kind: str) -> None:
    with path.open("rb") as fh:
        head = fh.read(64)
    if not head.startswith(SIGNATURES[kind]):
        raise click.ClickException(f"{path} does not look like a {kind} file (starts {head[:8]!r})")
    if kind == "pbf" and b"OSMHeader" not in head:
        raise click.ClickException(f"{path} has no OSMHeader block: not an OSM PBF file")


def ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()


@click.command()
@click.option("--url", required=True)
@click.option("--output", required=True)
@click.option("--kind", type=click.Choice(sorted(SIGNATURES)), required=True, help="expected file format")
@click.option("--retries", default=3, show_default=True)
def main(url, output, kind, retries):
    out = Path(output)
    if out.exists():
        check_format(out, kind)
        click.echo(f"{out} exists: not downloaded again")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=120, context=ssl_context()) as response, part.open("wb") as fh:
                expected = response.headers.get("Content-Length")
                shutil.copyfileobj(response, fh, length=1 << 20)
            size = part.stat().st_size
            if expected is not None and int(expected) != size:
                raise OSError(f"truncated: {size} of {expected} bytes")
            break
        except OSError as error:
            part.unlink(missing_ok=True)
            if attempt == retries:
                raise click.ClickException(f"Download of {url} failed: {error}") from error
            time.sleep(10 * attempt)
    check_format(part, kind)
    digest = hashlib.sha256(part.read_bytes()).hexdigest()
    part.replace(out)
    meta = {
        "url": url,
        "downloaded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "bytes": out.stat().st_size,
        "sha256": digest,
    }
    out.with_name(out.name + ".source.json").write_text(json.dumps(meta, indent=2) + "\n")


if __name__ == "__main__":
    main()

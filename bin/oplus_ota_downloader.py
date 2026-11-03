#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import os
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict
from urllib.parse import urlparse, parse_qs, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm

USER_AGENT = "okhttp/5.3.2"
OTA_USER_ID = "oplus-ota|16000020"
OTA_DOMAINS: Tuple[str, ...] = (
    "allawnos",
    "allawntech",
    "allawnofs",
    "gauss",
    "compota",
    "component-ota",
)

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 30
WRITE_TIMEOUT = 30

DEFAULT_CHUNK = 1024 * 1024
DEFAULT_FILENAME = "downloadfile"

CD_RFC5987 = re.compile(r"filename\*\s*=\s*(?:[\w-]+'[^']*')?([^;]+)", re.IGNORECASE)
CD_QUOTED  = re.compile(r'filename\s*=\s*"([^"]+)"', re.IGNORECASE)
CD_PLAIN   = re.compile(r"filename\s*=\s*([^;]+)", re.IGNORECASE)

RETRYABLE_MSG = ("unexpected end of stream",)

METADATA_KEYS = {
    "product_name": ("post-build", "ro.product.name", "product"),
    "android_version": ("post-build-version", "post-sdk-level"),
    "security_patch": ("post-security-patch-level",),
    "ota_version": ("post-build-incremental", "post-build"),
}

logger = logging.getLogger("ota_downloader")

class SecureSession(requests.Session):
    def get_redirect_target(self, response):
        target = super().get_redirect_target(response)
        if target:
            resolved = requests.compat.urljoin(response.url, target)
            if urlparse(response.url).scheme == "https" and urlparse(resolved).scheme != "https":
                raise ValueError("Refusing HTTPS redirect to an insecure URL")
        return target

@dataclass
class ProbeResult:
    url: str
    original_url: str
    filename: str
    size: Optional[int]
    md5: Optional[str] = None
    is_ota_domain: bool = False
    is_expiring: bool = False
    expires_at: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)

class OtaDownloader:
    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout: int = READ_TIMEOUT,
        retries: int = 3,
        output_dir: str = ".",
        verbose: bool = False,
        connections: int = 4,
    ) -> None:
        self.output_dir = output_dir
        self.timeout = (CONNECT_TIMEOUT, timeout)
        self.retries = max(0, int(retries))
        self.verbose = verbose
        self.connections = max(1, min(8, int(connections)))
        self.proxy = proxy
        self.session = self._build_session(proxy, self.retries)
        os.makedirs(output_dir, exist_ok=True)

    def _build_session(self, proxy: Optional[str], retries: int) -> requests.Session:
        s = SecureSession()
        s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "identity"})
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["HEAD", "GET"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}
        return s

    def _is_ota_domain(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(label == d or label.startswith(d + "-") for label in host.split(".") for d in OTA_DOMAINS)

    def _build_headers(self, url: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self._is_ota_domain(url):
            headers["userId"] = OTA_USER_ID
        if extra:
            headers.update(extra)
        return headers

    def _parse_content_disposition(self, header: Optional[str]) -> Optional[str]:
        if not header:
            return None
        for rx in (CD_RFC5987, CD_QUOTED, CD_PLAIN):
            m = rx.search(header)
            if m:
                raw = m.group(1).strip().strip('"').strip("'")
                try:
                    name = unquote(raw)
                except Exception:
                    name = raw
                name = os.path.basename(name).strip()
                if name:
                    return name
        return None

    def _parse_expiring_url(self, url: str) -> Tuple[Optional[str], Optional[int]]:
        qs = parse_qs(urlparse(url).query)
        for key in ("x-oss-expires", "Expires"):
            for k in (key, key.lower()):
                if k in qs and qs[k]:
                    try:
                        return k, int(qs[k][0])
                    except (ValueError, TypeError):
                        return k, None
        return None, None

    def _is_retryable_exception(self, exc: BaseException) -> bool:
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return True
        if isinstance(exc, requests.exceptions.ChunkedEncodingError):
            return True
        msg = str(exc).lower()
        return any(s in msg for s in RETRYABLE_MSG)

    def resolve_url(
        self,
        url: str,
        custom_name: Optional[str] = None,
        md5: Optional[str] = None,
    ) -> ProbeResult:
        """HEAD (no-redirect) + GET (stream) probe to determine URL/size/name."""
        headers = self._build_headers(url)
        is_ota = self._is_ota_domain(url)

        final_url = url
        try:
            head = self.session.head(
                url, headers=headers, allow_redirects=False, timeout=self.timeout
            )
            loc = head.headers.get("Location")
            if loc:
                final_url = requests.compat.urljoin(url, loc)
                if urlparse(url).scheme == "https" and urlparse(final_url).scheme != "https":
                    raise ValueError("Refusing HTTPS redirect to an insecure URL")
                logger.debug("HEAD redirected to %s", final_url)
        except requests.RequestException as e:
            logger.debug("HEAD probe failed: %s — falling back to GET", e)
        size: Optional[int] = None
        cd_name: Optional[str] = None
        resp_headers: Dict[str, str] = {}
        try:
            with self.session.get(
                final_url,
                headers=self._build_headers(final_url),
                stream=True,
                allow_redirects=True,
                timeout=self.timeout,
            ) as r:
                r.raise_for_status()
                final_url = r.url
                resp_headers = dict(r.headers)
                cl = r.headers.get("Content-Length")
                if cl is not None:
                    try:
                        size = int(cl)
                    except ValueError:
                        size = None
                cd_name = self._parse_content_disposition(r.headers.get("Content-Disposition"))
        except requests.RequestException:
            raise

        name = custom_name or cd_name
        if not name:
            path_name = os.path.basename(urlparse(final_url).path)
            name = unquote(path_name) if path_name else None
        if not name:
            name = DEFAULT_FILENAME
        if name in (".", "..") or os.path.basename(name) != name or "\\" in name or any(ord(c) < 32 for c in name):
            raise ValueError("Filename must be a plain file name without path components")

        exp_key, exp_ts = self._parse_expiring_url(final_url)
        is_expiring = exp_key is not None

        return ProbeResult(
            url=final_url,
            original_url=url,
            filename=name,
            size=size,
            md5=md5,
            is_ota_domain=is_ota or self._is_ota_domain(final_url),
            is_expiring=is_expiring,
            expires_at=exp_ts,
            headers=resp_headers,
        )

    def download(
        self,
        url: str,
        filename: Optional[str] = None,
        md5: Optional[str] = None,
        resume: bool = True,
    ) -> bool:
        probe = self.resolve_url(url, custom_name=filename, md5=md5)
        filepath = os.path.join(self.output_dir, probe.filename)

        logger.info("→ %s", probe.url)
        logger.info("   file: %s   size: %s",
                    probe.filename,
                    f"{probe.size} bytes" if probe.size else "unknown")
        if probe.is_expiring:
            logger.info("   expiring link (expires_at=%s)", probe.expires_at)

        resume_offset = 0
        if resume and os.path.exists(filepath):
            resume_offset = os.path.getsize(filepath)
            if probe.size is not None and resume_offset == probe.size and probe.size > 0:
                logger.info("   already complete (%d bytes) — skipping download", resume_offset)
                return self._maybe_verify(filepath, probe.md5)
            if probe.size is not None and resume_offset > probe.size:
                resume_offset = 0

        attempt = 0
        if self.connections > 1 and probe.size and probe.size >= 32 * 1024 * 1024 and resume_offset == 0:
            try:
                if self._parallel_download(probe, filepath):
                    return self._maybe_verify(filepath, probe.md5)
            except (requests.RequestException, OSError, ValueError) as exc:
                logger.error("Parallel download failed (%s): %s", type(exc).__name__, exc)
                return False
        while True:
            attempt += 1
            try:
                ok = self._do_download_attempt(probe, filepath, resume_offset, resume)
                if not ok:
                    return False
                break
            except Exception as e:  # noqa: BLE001
                if attempt > self.retries or not self._is_retryable_exception(e):
                    logger.error("Download failed (%s): %s", type(e).__name__, e)
                    return False
                logger.warning("Retry %d/%d after error: %s", attempt, self.retries, e)
                resume_offset = os.path.getsize(filepath) if resume and os.path.exists(filepath) else 0

        return self._maybe_verify(filepath, probe.md5)

    def _parallel_download(self, probe: ProbeResult, filepath: str) -> bool:
        """Stream disjoint ranges into a temporary file; publish only on success."""
        with self.session.get(
            probe.url, headers=self._build_headers(probe.url, {"Range": "bytes=0-0", "Accept-Encoding": "identity"}),
            stream=True, timeout=self.timeout,
        ) as reply:
            if reply.status_code == 200:
                logger.info("Server ignores Range; using sequential download")
                return False
            reply.raise_for_status()
            if reply.status_code != 206 or reply.headers.get("Content-Range") != f"bytes 0-0/{probe.size}":
                raise ValueError("Invalid range capability response")
            validator = reply.headers.get("ETag") or reply.headers.get("Last-Modified")
            if validator and validator.startswith('W/'):
                validator = None

        temporary = filepath + ".part"
        try:
            with open(temporary, "wb") as output:
                output.truncate(probe.size)
            block = (probe.size + self.connections - 1) // self.connections

            def fetch(start):
                end = min(probe.size, start + block) - 1
                headers = self._build_headers(probe.url, {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
                if validator:
                    headers["If-Range"] = validator
                with self._build_session(self.proxy, self.retries) as session:
                    for attempt in range(self.retries + 1):
                        try:
                            with session.get(probe.url, headers=headers, stream=True, timeout=self.timeout) as reply:
                                reply.raise_for_status()
                                expected = f"bytes {start}-{end}/{probe.size}"
                                if reply.status_code != 206 or reply.headers.get("Content-Range") != expected:
                                    raise ValueError("Server returned an incorrect range or changed file")
                                if reply.headers.get('Content-Encoding', 'identity') != 'identity':
                                    raise ValueError("Encoded range response is not supported")
                                written = 0
                                with open(temporary, "r+b") as output:
                                    output.seek(start)
                                    for chunk in reply.iter_content(DEFAULT_CHUNK):
                                        if written + len(chunk) > end - start + 1:
                                            raise ValueError("Range response exceeds requested length")
                                        output.write(chunk)
                                        written += len(chunk)
                                if written != end - start + 1:
                                    raise requests.exceptions.ChunkedEncodingError("Incomplete range")
                                return
                        except requests.RequestException as exc:
                            if attempt == self.retries or not self._is_retryable_exception(exc):
                                raise

            logger.info("Downloading with %d connections", self.connections)
            with ThreadPoolExecutor(max_workers=self.connections) as pool:
                list(pool.map(fetch, range(0, probe.size, block)))
            os.replace(temporary, filepath)
            return True
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def _do_download_attempt(
        self,
        probe: ProbeResult,
        filepath: str,
        resume_offset: int,
        resume: bool,
    ) -> bool:
        headers = self._build_headers(probe.url)
        headers["Accept-Encoding"] = "identity"
        if resume and resume_offset > 0:
            headers["Range"] = f"bytes={resume_offset}-"

        with self.session.get(
            probe.url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=self.timeout,
        ) as r:
            status = r.status_code
            logger.debug("HTTP %s for %s", status, probe.url)

            if status in (401, 403):
                logger.warning("HTTP %s — link expired or unauthorized: %s", status, probe.url)
                return False

            if status == 416:
                raise ValueError("Server rejected Range; file completeness cannot be verified")

            if status == 200 and resume_offset > 0:
                logger.info("Server did not honor Range; restarting download from byte 0.")
                resume_offset = 0
                mode = "wb"
            elif status == 206:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", r.headers.get("Content-Range", ""))
                if not match:
                    raise ValueError("Missing or malformed Content-Range")
                start, end, length = map(int, match.groups())
                if start != resume_offset or end < start or end != length - 1:
                    raise ValueError("Content-Range does not match requested file suffix")
                if probe.size is not None and length != probe.size:
                    raise ValueError("Remote file size changed while downloading")
                probe.size = length
                mode = "ab" if resume_offset else "wb"
            elif status == 200:
                mode = "wb"
            else:
                r.raise_for_status()
                return False

            # Determine total for tqdm
            cl = r.headers.get("Content-Length")
            stream_len: Optional[int] = None
            if cl is not None:
                try:
                    stream_len = int(cl)
                except ValueError:
                    stream_len = None

            if stream_len is not None and mode == "ab":
                total = resume_offset + stream_len
            elif probe.size is not None:
                total = probe.size
            else:
                total = stream_len

            with open(filepath, mode) as f:
                self._download_stream(r, f, total, resume_offset if mode == "ab" else 0)
            actual = os.path.getsize(filepath)
            if actual == 0 or (total is not None and actual != total):
                raise requests.exceptions.ChunkedEncodingError("Downloaded size does not match expected size")

        return True

    def _download_stream(
        self,
        response: requests.Response,
        file_obj,
        total: Optional[int],
        resume_offset: int,
    ) -> None:
        desc = os.path.basename(file_obj.name)
        with tqdm(
            total=total,
            initial=resume_offset,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=desc,
            dynamic_ncols=True,
        ) as bar:
            for chunk in response.iter_content(chunk_size=DEFAULT_CHUNK):
                if not chunk:
                    continue
                file_obj.write(chunk)
                bar.update(len(chunk))

    def _maybe_verify(self, filepath: str, expected: Optional[str]) -> bool:
        if not expected:
            logger.info("✓ done: %s", filepath)
            return True
        ok = self.verify_md5(filepath, expected)
        if ok:
            logger.info("✓ MD5 OK: %s", filepath)
        else:
            logger.error("✗ MD5 MISMATCH: %s", filepath)
        return ok

    def verify_md5(self, filepath: str, expected: str) -> bool:
        h = hashlib.md5()
        size = os.path.getsize(filepath)
        with open(filepath, "rb") as f, tqdm(
            total=size, unit="B", unit_scale=True, unit_divisor=1024, desc="MD5",
            dynamic_ncols=True,
        ) as bar:
            for chunk in iter(lambda: f.read(DEFAULT_CHUNK), b""):
                h.update(chunk)
                bar.update(len(chunk))
        got = h.hexdigest()
        ok = got.lower() == expected.lower().strip()
        logger.info("MD5 %s (expected %s) → %s", got, expected, "OK" if ok else "MISMATCH")
        return ok


    def fetch_metadata(self, url: str) -> Dict[str, str]:
        """Read META-INF/com/android/metadata via a tail Range request."""
        probe = self.resolve_url(url)
        if not probe.size:
            raise RuntimeError("Cannot fetch metadata: unknown file size for %s" % url)

        downloader = self

        class RemoteZip(io.RawIOBase):
            def __init__(self):
                self.position = 0

            def seekable(self):
                return True

            def readable(self):
                return True

            def tell(self):
                return self.position

            def seek(self, offset, whence=0):
                target = offset + (self.position if whence == 1 else probe.size if whence == 2 else 0)
                if target < 0:
                    raise ValueError("Negative seek")
                self.position = target
                return target

            def read(self, size=-1):
                end = probe.size if size < 0 else min(probe.size, self.position + size)
                if end <= self.position:
                    return b""
                if end - self.position > 4 * 1024 * 1024:
                    raise ValueError("ZIP metadata read exceeds 4 MiB limit")
                data = downloader._range_get(probe.url, self.position, end - 1)
                self.position = end
                return data

        with zipfile.ZipFile(RemoteZip()) as zf:
            target = "META-INF/com/android/metadata"
            info = zf.getinfo(target)
            if info.file_size > 4 * 1024 * 1024:
                raise ValueError("OTA metadata exceeds 4 MiB limit")
            raw = zf.read(info).decode("utf-8", errors="replace")
            return self._parse_metadata_properties(raw)

    def _range_get(self, url: str, start: int, end: int) -> bytes:
        headers = self._build_headers(url, {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
        with self.session.get(
            url, headers=headers, stream=True, allow_redirects=True, timeout=self.timeout
        ) as r:
            r.raise_for_status()
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", r.headers.get("Content-Range", ""))
            if r.status_code != 206 or not match or tuple(map(int, match.groups()[:2])) != (start, end):
                raise ValueError("Server does not support the requested byte range")
            data = r.raw.read(end - start + 2)
            if len(data) != end - start + 1:
                raise ValueError("Incomplete or oversized range response")
            return data

    def _parse_metadata_properties(self, text: str) -> Dict[str, str]:
        props: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()

        result: Dict[str, str] = {"_raw": text}
        for friendly, candidates in METADATA_KEYS.items():
            for c in candidates:
                if c in props:
                    result[friendly] = props[c]
                    break
        result.update({f"raw:{k}": v for k, v in props.items()})
        return result

def read_batch(path: str) -> List[str]:
    urls: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            urls.append(s)
    return urls

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ota_downloader.py",
        description="OPPO/OnePlus/Realme OTA firmware downloader "
                    "(mirrors OPlusDownloader HTTP behavior).",
    )
    p.add_argument("urls", metavar="URL", nargs="*", help="One or more firmware URLs")
    p.add_argument("-o", "--output", default=".", help="Output directory (default: current dir)")
    p.add_argument("-n", "--name", help="Custom filename")
    p.add_argument("--md5", help="Expected MD5 for verification")
    p.add_argument("--proxy", help="Proxy URL (http://host:port)")
    p.add_argument("--no-resume", action="store_true", help="Disable resume support")
    p.add_argument("--timeout", type=int, default=READ_TIMEOUT,
                   help=f"Read timeout in seconds (default: {READ_TIMEOUT})")
    p.add_argument("--retries", type=int, default=3, help="Max retries (default: 3)")
    p.add_argument("--connections", type=int, choices=range(1, 9), default=4,
                   help="Parallel range connections for files >=32 MiB (default: 4; 1 disables)")
    p.add_argument("--batch", help="Read URLs from file (one per line)")
    p.add_argument("--metadata", action="store_true",
                   help="Fetch and display OTA metadata from ZIP")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    return p

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    urls: List[str] = list(args.urls)
    if args.batch:
        try:
            urls.extend(read_batch(args.batch))
        except OSError as e:
            parser.error(f"Cannot read batch file: {e}")

    if not urls:
        parser.error("No URLs given")

    dl = OtaDownloader(
        proxy=args.proxy,
        timeout=args.timeout,
        retries=args.retries,
        output_dir=args.output,
        verbose=args.verbose,
        connections=args.connections,
    )

    ok_count = 0
    fail_count = 0

    for i, url in enumerate(urls, 1):
        logger.info("[%d/%d] %s", i, len(urls), url)
        try:
            if args.metadata:
                meta = dl.fetch_metadata(url)
                print(f"\n=== Metadata for {url} ===")
                for key in ("product_name", "android_version", "security_patch", "ota_version"):
                    if key in meta:
                        print(f"  {key:20s} {meta[key]}")
                if args.verbose:
                    print("  --- raw properties ---")
                    for k, v in meta.items():
                        if k.startswith("raw:"):
                            print(f"  {k[4:]:30s} {v}")
                print()
                ok_count += 1
            else:
                success = dl.download(
                    url,
                    filename=args.name if len(urls) == 1 else None,
                    md5=args.md5 if len(urls) == 1 else None,
                    resume=not args.no_resume,
                )
                if success:
                    ok_count += 1
                else:
                    fail_count += 1
        except KeyboardInterrupt:
            logger.warning("Interrupted by user.")
            return 130
        except Exception as e:  # noqa: BLE001
            logger.error("Error processing %s: %s", url, e)
            fail_count += 1

    logger.info("--- summary: %d ok, %d failed ---", ok_count, fail_count)
    return 0 if fail_count == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
          

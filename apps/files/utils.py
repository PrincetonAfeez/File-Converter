# "Upload validation, MIME sniffing, and hashing."
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")

# Acceptable detected MIME (prefixes) per declared source extension. Content sniffing is
# imperfect, so the allow-lists are deliberately generous; ``application/octet-stream`` and
# an empty result are always tolerated to avoid false rejections.
_ALWAYS_OK = {"", "application/octet-stream"}
MIME_ALLOWLIST: dict[str, set[str]] = {
    "png": {"image/"},
    "jpg": {"image/"},
    "jpeg": {"image/"},
    "webp": {"image/"},
    "bmp": {"image/"},
    "tiff": {"image/"},
    "csv": {"text/", "application/csv", "application/vnd.ms-excel"},
    "json": {"text/", "application/json"},
    "html": {"text/"},
    # Office/OpenDocument containers are ZIP archives under the hood.
    "xlsx": {"application/zip", "application/vnd.openxmlformats", "application/vnd.ms-excel"},
    "docx": {"application/zip", "application/vnd.openxmlformats", "application/msword"},
    "pptx": {"application/zip", "application/vnd.openxmlformats", "application/vnd.ms-powerpoint"},
    "odt": {"application/zip", "application/vnd.oasis"},
    "wav": {"audio/", "video/"},
    "mp3": {"audio/", "video/"},
    "mp4": {"video/", "audio/", "application/mp4"},
    "mov": {"video/", "audio/"},
    "avi": {"video/", "audio/"},
}


def sanitize_filename(name: str) -> str:
    name = Path(name).name.strip().replace("\\", "_").replace("/", "_")
    name = SAFE_FILENAME_RE.sub("_", name)
    return name[:240] or "upload"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_upload(uploaded_file) -> str:
    """Checksum an in-flight upload without relying on it being written to disk yet."""
    digest = hashlib.sha256()
    try:
        pos = uploaded_file.tell()
    except (OSError, AttributeError):
        pos = 0
    try:
        uploaded_file.seek(0)
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
    finally:
        try:
            uploaded_file.seek(pos)
        except Exception:
            uploaded_file.seek(0)
    return digest.hexdigest()


def validate_upload_size(uploaded_file) -> None:
    if uploaded_file.size <= 0:
        raise ValueError("Uploaded file is empty.")
    max_bytes = settings.FILECONVERTER_MAX_UPLOAD_BYTES
    if uploaded_file.size > max_bytes:
        max_mb = max_bytes / 1024 / 1024
        raise ValueError(f"File is larger than the configured {max_mb:.0f} MB limit.")


_MAGIC_UNSET = object()
_magic_module = _MAGIC_UNSET


def _magic():
    """Import python-magic once, caching the result (and warning) across calls."""
    global _magic_module
    if _magic_module is _MAGIC_UNSET:
        try:
            import magic as _m

            _magic_module = _m
        except Exception:  # pragma: no cover - libmagic not installed in this runtime
            logger.warning("python-magic/libmagic unavailable; content sniffing disabled")
            _magic_module = None
    return _magic_module


def mime_scanner_available() -> bool:
    """True when libmagic can be loaded for content-type sniffing."""
    return _magic() is not None


def detect_mime(path: Path) -> str:
    """Return the libmagic-detected MIME type, or "" if detection is unavailable."""
    magic = _magic()
    if magic is None:
        return ""
    try:
        return magic.from_file(str(path), mime=True) or ""
    except Exception:
        logger.exception("MIME detection failed for %s", path)
        return ""


def detect_mime_from_upload(uploaded_file) -> str:
    """Sniff an in-flight upload's MIME without persisting it first."""
    magic = _magic()
    if magic is None:
        return ""
    try:
        pos = uploaded_file.tell()
    except (OSError, AttributeError):
        pos = 0
    try:
        uploaded_file.seek(0)
        head = uploaded_file.read(4096)
    except Exception:
        logger.exception("Could not read upload buffer for MIME detection")
        return ""
    finally:
        try:
            uploaded_file.seek(pos)
        except Exception:
            uploaded_file.seek(0)
    if isinstance(head, str):
        head = head.encode("utf-8", "ignore")
    try:
        return magic.from_buffer(head, mime=True) or ""
    except Exception:
        logger.exception("MIME detection from buffer failed")
        return ""


def validate_content_type(source_format: str, detected: str) -> None:
    """Reject an upload whose sniffed content clearly contradicts its extension."""
    if not detected or detected in _ALWAYS_OK:
        return
    allowed = MIME_ALLOWLIST.get(source_format)
    if allowed is None:
        return
    if any(detected.startswith(prefix) for prefix in allowed):
        return
    raise ValueError(
        f"File content ({detected}) does not match the .{source_format} extension."
    )


def client_ip_from_request(request) -> str:
    """Best-effort real client IP, honoring X-Forwarded-For behind trusted proxies.

    ``FILECONVERTER_TRUSTED_PROXY_COUNT`` is the number of trusted proxies in front of the
    app; the client IP is taken that many hops from the right of X-Forwarded-For. When 0
    (no proxy) or the header is absent, REMOTE_ADDR is used. This prevents a single proxy
    IP from collapsing all clients into one throttle bucket.
    """
    proxy_count = getattr(settings, "FILECONVERTER_TRUSTED_PROXY_COUNT", 0)
    remote_addr = request.META.get("REMOTE_ADDR", "unknown")
    if proxy_count <= 0:
        return remote_addr
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not forwarded:
        return remote_addr
    parts = [ip.strip() for ip in forwarded.split(",") if ip.strip()]
    if not parts:
        return remote_addr
    # The rightmost `proxy_count` entries are our own proxies; the client is the hop before.
    index = len(parts) - proxy_count - 1
    if index < 0:
        index = 0
    return parts[index]


def extension_for_path(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def ensure_parent(path: Path) -> None:
    os.makedirs(path.parent, exist_ok=True)

"""Load the photo for a new Wardrowbe item from a local file or a URL."""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# What Wardrowbe accepts for a new item. It rejects other types and anything
# over 20 MB, so both are checked here before uploading.
IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}
_EXTENSIONS = {content_type: ext for ext, content_type in reversed(IMAGE_TYPES.items())}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_DOWNLOAD_TIMEOUT = 60

# (body, filename, content type)
type Image = tuple[bytes, str, str]


def _unsupported(source: str) -> ServiceValidationError:
    return ServiceValidationError(
        f"{source} is not a JPEG, PNG, WebP or HEIC image, which Wardrowbe requires"
    )


def _too_large(source: str) -> ServiceValidationError:
    return ServiceValidationError(f"{source} is larger than Wardrowbe's 20 MB limit")


async def async_read_image_file(hass: HomeAssistant, path: str) -> Image:
    content_type = IMAGE_TYPES.get(os.path.splitext(path)[1].lower())
    if content_type is None:
        raise _unsupported(path)

    def _read() -> bytes:
        # is_allowed_path resolves symlinks on disk, so it runs here too.
        if not hass.config.is_allowed_path(path):
            raise ServiceValidationError(
                f"{path} is not in a folder listed in allowlist_external_dirs"
            )
        try:
            with open(path, "rb") as fh:
                return fh.read(MAX_UPLOAD_BYTES + 1)
        except OSError as err:
            raise ServiceValidationError(f"Cannot read {path}: {err}") from err

    body = await hass.async_add_executor_job(_read)
    if len(body) > MAX_UPLOAD_BYTES:
        raise _too_large(path)
    return body, os.path.basename(path), content_type


async def async_download_image(hass: HomeAssistant, url: str) -> Image:
    url_path = PurePosixPath(urlsplit(url).path)
    try:
        async with async_get_clientsession(hass).get(
            url, timeout=aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                raise HomeAssistantError(f"Downloading {url} failed: HTTP {resp.status}")
            # Servers often send images as application/octet-stream, so fall
            # back to the extension in the URL.
            content_type = (
                resp.content_type
                if resp.content_type in _EXTENSIONS
                else IMAGE_TYPES.get(url_path.suffix.lower())
            )
            if content_type is None:
                raise _unsupported(url)
            body = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                body.extend(chunk)
                if len(body) > MAX_UPLOAD_BYTES:
                    raise _too_large(url)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise HomeAssistantError(f"Downloading {url} failed: {err}") from err
    # Wardrowbe reads the format from the file name, so it must match the type.
    filename = f"{url_path.stem or 'image'}{_EXTENSIONS[content_type]}"
    return bytes(body), filename, content_type


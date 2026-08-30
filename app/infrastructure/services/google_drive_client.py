from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from io import FileIO
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from loguru import logger

from app.config import settings

_DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive",)
_FOLDER_ID_RE = re.compile(r"[-\w]{10,}")
_DRIVE_FOLDER_URL_RE = re.compile(
    r"drive\.google\.com/(?:drive/(?:u/\d+/)?folders/|open\?id=)([a-zA-Z0-9_-]+)"
)


@dataclass(frozen=True)
class DriveVideo:
    file_id: str
    name: str
    mime_type: str
    created_time: str


def parse_folder_id(url_or_id: str) -> str:
    raw = (url_or_id or "").strip()
    if not raw:
        return ""
    match = _DRIVE_FOLDER_URL_RE.search(raw)
    if match:
        return match.group(1)
    if _FOLDER_ID_RE.fullmatch(raw):
        return raw
    return ""


def _load_service_account_info() -> dict[str, Any]:
    b64 = (settings.google_drive.service_account_json_b64 or "").strip()
    if b64:
        decoded = base64.b64decode(b64)
        return json.loads(decoded.decode("utf-8"))
    path = (settings.google_drive.service_account_json or "").strip()
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    raise RuntimeError(
        "Google Drive credentials missing: set GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON "
        "or GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_B64"
    )


def _build_drive_service():
    info = _load_service_account_info()
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=_DRIVE_SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_videos_sync(folder_id: str) -> list[DriveVideo]:
    service = _build_drive_service()
    query = (
        f"'{folder_id}' in parents and trashed=false and "
        "mimeType contains 'video/'"
    )
    videos: list[DriveVideo] = []
    page_token: str | None = None
    while True:
        response = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, createdTime)",
                orderBy="createdTime",
                pageSize=100,
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("files") or []:
            file_id = str(item.get("id") or "").strip()
            if not file_id:
                continue
            videos.append(
                DriveVideo(
                    file_id=file_id,
                    name=str(item.get("name") or "").strip() or file_id,
                    mime_type=str(item.get("mimeType") or "").strip(),
                    created_time=str(item.get("createdTime") or "").strip(),
                )
            )
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return videos


def _download_file_sync(file_id: str, dest_path: Path) -> Path:
    service = _build_drive_service()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with FileIO(str(dest_path), "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path


def _delete_file_sync(file_id: str) -> None:
    service = _build_drive_service()
    service.files().delete(fileId=file_id).execute()


async def delete_file(file_id: str) -> None:
    fid = (file_id or "").strip()
    if not fid:
        return
    try:
        await asyncio.to_thread(_delete_file_sync, fid)
    except Exception as exc:
        logger.exception(f"Google Drive delete failed file_id={fid}: {exc}")
        raise


async def list_videos(folder_id: str) -> list[DriveVideo]:
    fid = parse_folder_id(folder_id) or folder_id.strip()
    if not fid:
        return []
    try:
        return await asyncio.to_thread(_list_videos_sync, fid)
    except Exception as exc:
        logger.exception(f"Google Drive list_videos failed folder_id={fid}: {exc}")
        raise


async def download_file(file_id: str, dest_path: Path) -> Path:
    try:
        return await asyncio.to_thread(_download_file_sync, file_id, dest_path)
    except Exception as exc:
        logger.exception(f"Google Drive download failed file_id={file_id}: {exc}")
        raise

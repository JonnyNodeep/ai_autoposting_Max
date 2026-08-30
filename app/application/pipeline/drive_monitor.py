from __future__ import annotations

import copy
from typing import Any

from app.infrastructure.services.google_drive_client import parse_folder_id

DEFAULT_DRIVE_VIDEO: dict[str, Any] = {
    "enabled": False,
    "folder_id": "",
    "fixed_caption": "",
    "low_stock_threshold": 5,
    "low_stock_notified_at_remaining": None,
    "delete_after_publish": True,
}


def normalize_drive_video(raw: Any) -> dict[str, Any]:
    src = dict(raw or {})
    out = dict(DEFAULT_DRIVE_VIDEO)
    out["enabled"] = bool(src.get("enabled", False))
    folder_raw = str(src.get("folder_id") or "").strip()
    out["folder_id"] = parse_folder_id(folder_raw) or folder_raw
    out["fixed_caption"] = str(src.get("fixed_caption") or "").strip()[:4000]
    try:
        out["low_stock_threshold"] = max(1, int(src.get("low_stock_threshold") or 5))
    except (TypeError, ValueError):
        out["low_stock_threshold"] = 5
    notified = src.get("low_stock_notified_at_remaining")
    if notified is None or notified == "":
        out["low_stock_notified_at_remaining"] = None
    else:
        try:
            out["low_stock_notified_at_remaining"] = int(notified)
        except (TypeError, ValueError):
            out["low_stock_notified_at_remaining"] = None
    if "delete_after_publish" in src:
        out["delete_after_publish"] = bool(src.get("delete_after_publish"))
    else:
        out["delete_after_publish"] = bool(DEFAULT_DRIVE_VIDEO["delete_after_publish"])
    return out


def is_drive_trigger(blocks_config: dict[str, Any] | None) -> bool:
    cfg = normalize_drive_video((blocks_config or {}).get("drive_video"))
    return bool(cfg.get("enabled") and cfg.get("folder_id"))


def apply_drive_video_patch(
    blocks_config: dict[str, Any] | None, patch: dict[str, Any]
) -> dict[str, Any]:
    out = copy.deepcopy(blocks_config or {})
    current = normalize_drive_video(out.get("drive_video"))
    current.update(patch)
    normalized = normalize_drive_video(current)
    out["drive_video"] = normalized
    if out.get("version") == 2:
        steps = list(out.get("steps") or [])
        for step in steps:
            if step.get("type") != "drive_video":
                continue
            cfg = dict(step.get("config") or {})
            for key in (
                "folder_id",
                "fixed_caption",
                "low_stock_threshold",
                "low_stock_notified_at_remaining",
                "delete_after_publish",
            ):
                if key in normalized:
                    cfg[key] = normalized[key]
            step["config"] = cfg
            step["enabled"] = bool(normalized.get("enabled"))
        out["steps"] = steps
    return out

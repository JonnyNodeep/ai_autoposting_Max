from __future__ import annotations

import copy
import uuid
from typing import Any

# Execution order for content steps (schedule is a trigger, not a step).
STEP_ORDER = ("image_prompt", "image_gen", "video_gen", "post_gen")

# Future: content_plan block will register here as another step type.
# KNOWN_FUTURE_BLOCKS = ("content_plan",)

_CONFIG_KEYS = {
    "image_gen": ("model",),
    "image_prompt": ("mode", "user_description", "generated_prompt", "instruction", "use_visual_style"),
    "video_gen": (
        "model",
        "duration",
        "mode",
        "resolution",
        "prompt_mode",
        "user_description",
        "generated_prompt",
    ),
    "post_gen": ("mode", "user_input", "generated_post", "add_channel_link", "bold_headings", "use_emoji", "comments_enabled"),
    "schedule": ("frequency", "times"),
}


def _new_step_id() -> str:
    return uuid.uuid4().hex[:12]


def _split_block_dict(block_id: str, data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    data = dict(data or {})
    enabled = bool(data.pop("enabled", False))
    known = _CONFIG_KEYS.get(block_id, ())
    config = {k: data[k] for k in known if k in data}
    # Preserve any extra keys in config for forward compatibility
    for k, v in data.items():
        if k not in config and k != "enabled":
            config[k] = v
    return enabled, config


def is_v2(config: Any) -> bool:
    return isinstance(config, dict) and config.get("version") == 2 and "steps" in config


def normalize_blocks_config(raw: Any) -> dict[str, Any]:
    """Convert legacy UI dict or already-v2 config into canonical v2."""
    if raw is None:
        raw = {}

    if is_v2(raw):
        steps = []
        for step in raw.get("steps") or []:
            steps.append(
                {
                    "id": step.get("id") or _new_step_id(),
                    "type": step["type"],
                    "enabled": bool(step.get("enabled", False)),
                    "config": copy.deepcopy(step.get("config") or {}),
                }
            )
        schedule = copy.deepcopy(raw.get("schedule") or {"enabled": False, "frequency": "daily", "times": []})
        if "enabled" not in schedule:
            schedule["enabled"] = False
        return {"version": 2, "steps": steps, "schedule": schedule}

    # Legacy flat dict: DEFAULT_BLOCKS shape
    if not isinstance(raw, dict):
        raw = {}

    steps: list[dict[str, Any]] = []
    for block_type in STEP_ORDER:
        block_data = raw.get(block_type) or {}
        enabled, config = _split_block_dict(block_type, block_data)
        steps.append(
            {
                "id": _new_step_id(),
                "type": block_type,
                "enabled": enabled,
                "config": config,
            }
        )

    sched_raw = raw.get("schedule") or {}
    enabled, sched_cfg = _split_block_dict("schedule", sched_raw)
    schedule = {"enabled": enabled, **sched_cfg}
    if "frequency" not in schedule:
        schedule["frequency"] = "daily"
    if "times" not in schedule:
        schedule["times"] = []

    return {"version": 2, "steps": steps, "schedule": schedule}


def steps_to_ui_dict(config: Any) -> dict[str, Any]:
    """Adapt v2 (or legacy) config back to the flat dict the bot UI expects."""
    v2 = normalize_blocks_config(config)
    ui: dict[str, Any] = {}
    for step in v2["steps"]:
        ui[step["type"]] = {"enabled": step["enabled"], **copy.deepcopy(step["config"])}
    sched = v2.get("schedule") or {}
    ui["schedule"] = {
        "enabled": bool(sched.get("enabled", False)),
        "frequency": sched.get("frequency", "daily"),
        "times": list(sched.get("times") or []),
    }
    return ui


def ui_dict_to_v2(ui_blocks: dict[str, Any]) -> dict[str, Any]:
    """Convert bot FSM flat blocks dict into v2 for persistence."""
    return normalize_blocks_config(ui_blocks)


def get_step_config(v2: dict[str, Any], block_type: str) -> dict[str, Any]:
    for step in v2.get("steps") or []:
        if step.get("type") == block_type:
            return {"enabled": step.get("enabled", False), **(step.get("config") or {})}
    return {}

"""TTS style presets for gpt-4o-mini-tts instructions."""

from __future__ import annotations

TTS_INSTRUCTION_PRESETS: dict[str, str] = {
    "bedtime": (
        "Speak softly and calmly, like a bedtime storyteller for a young child. "
        "Warm, gentle, slow, soothing. No shouting, no scary intensity."
    ),
    "warm": (
        "Speak warmly and vividly, like a friendly storyteller reading to children. "
        "Expressive but gentle, clear diction, playful without being loud."
    ),
    "whisper": (
        "Speak in a soft near-whisper, intimate and calm, as if sharing a quiet "
        "bedtime secret. Very gentle pacing, no harsh sounds."
    ),
}

TTS_INSTRUCTION_PRESET_LABELS: dict[str, str] = {
    "bedtime": "на ночь",
    "warm": "тепло",
    "whisper": "шёпот",
    "custom": "свой",
}

DEFAULT_TTS_INSTRUCTIONS = TTS_INSTRUCTION_PRESETS["bedtime"]
DEFAULT_TTS_INSTRUCTIONS_PRESET = "bedtime"

"""TTS provider voice/role catalogs for OpenAI and Yandex SpeechKit."""

from __future__ import annotations

TTS_PROVIDER_OPENAI = "openai"
TTS_PROVIDER_SPEECHKIT = "speechkit"
TTS_PROVIDERS = (TTS_PROVIDER_OPENAI, TTS_PROVIDER_SPEECHKIT)

DEFAULT_TTS_PROVIDER = TTS_PROVIDER_SPEECHKIT
DEFAULT_SPEECHKIT_VOICE = "dasha"
DEFAULT_SPEECHKIT_SPEED = 0.9
DEFAULT_SPEECHKIT_ROLE = "neutral"
DEFAULT_OPENAI_VOICE = "shimmer"
DEFAULT_OPENAI_SPEED = 0.85

OPENAI_TTS_VOICES: list[tuple[str, str]] = [
    ("shimmer", "Shimmer"),
    ("nova", "Nova"),
    ("sage", "Sage"),
    ("echo", "Echo"),
    ("onyx", "Onyx"),
    ("alloy", "Alloy"),
    ("ash", "Ash"),
    ("coral", "Coral"),
    ("fable", "Fable"),
    ("ballad", "Ballad"),
    ("verse", "Verse"),
    ("marin", "Marin"),
    ("cedar", "Cedar"),
]

SPEECHKIT_VOICES: list[tuple[str, str]] = [
    ("dasha", "Даша"),
    ("alena", "Алёна"),
    ("marina", "Марина"),
    ("masha", "Маша"),
    ("jane", "Джейн"),
    ("omazh", "Омаж"),
    ("filipp", "Филипп"),
    ("ermil", "Ермил"),
    ("zahar", "Захар"),
]

SPEECHKIT_ROLES: list[tuple[str, str]] = [
    ("neutral", "Нейтральный"),
    ("good", "Радостный"),
    ("friendly", "Дружелюбный"),
    ("strict", "Строгий"),
    ("whisper", "Шёпот"),
]

# Roles supported per voice (from Yandex SpeechKit docs). Missing → no role hint.
SPEECHKIT_VOICE_ROLES: dict[str, frozenset[str]] = {
    "dasha": frozenset({"neutral", "good", "friendly"}),
    "alena": frozenset({"neutral", "good"}),
    "marina": frozenset({"neutral", "friendly", "whisper"}),
    "masha": frozenset({"neutral", "good", "strict", "friendly"}),
    "jane": frozenset({"neutral", "good", "evil"}),
    "omazh": frozenset({"neutral", "good", "evil"}),
    "ermil": frozenset({"neutral", "good"}),
    "zahar": frozenset({"good", "evil"}),
    # filipp: no roles in docs
}

TTS_SPEEDS = (0.75, 0.85, 0.9, 0.95, 1.0, 1.1)

SPEECHKIT_MAX_CHARS = 2500


def openai_voice_ids() -> set[str]:
    return {v for v, _ in OPENAI_TTS_VOICES}


def speechkit_voice_ids() -> set[str]:
    return {v for v, _ in SPEECHKIT_VOICES}


def voice_label(provider: str, voice_id: str) -> str:
    catalog = SPEECHKIT_VOICES if provider == TTS_PROVIDER_SPEECHKIT else OPENAI_TTS_VOICES
    for vid, label in catalog:
        if vid == voice_id:
            return label
    return voice_id


def role_label(role: str) -> str:
    for rid, label in SPEECHKIT_ROLES:
        if rid == role:
            return label
    return role


def roles_for_voice(voice: str) -> list[tuple[str, str]]:
    allowed = SPEECHKIT_VOICE_ROLES.get(voice)
    if not allowed:
        return [("neutral", "Нейтральный")]
    return [(rid, label) for rid, label in SPEECHKIT_ROLES if rid in allowed] or [
        (sorted(allowed)[0], sorted(allowed)[0])
    ]


def resolve_role(voice: str, role: str | None) -> str | None:
    """Return role to send, or None if voice has no roles / invalid."""
    allowed = SPEECHKIT_VOICE_ROLES.get(voice)
    if not allowed:
        return None
    r = (role or DEFAULT_SPEECHKIT_ROLE).strip() or DEFAULT_SPEECHKIT_ROLE
    if r not in allowed:
        return DEFAULT_SPEECHKIT_ROLE if DEFAULT_SPEECHKIT_ROLE in allowed else next(iter(allowed))
    return r

"""Tests for extended Sunor API client helpers."""

from app.infrastructure.services.sunor_client import (
    build_continue_input,
    build_custom_music_input,
    build_inspiration_input,
    build_instrumental_input,
    parse_lyrics_text,
    pick_track,
    MusicTrack,
)


def test_build_inspiration_input():
    inp = build_inspiration_input(
        gpt_description_prompt="A chill lofi beat",
        make_instrumental=True,
    )
    assert inp["gpt_description_prompt"] == "A chill lofi beat"
    assert inp["make_instrumental"] is True


def test_build_custom_music_input_with_tags():
    inp = build_custom_music_input(
        prompt="[Verse]\nHello",
        tags="pop, upbeat",
        negative_tags="metal",
        title="Song",
        vocal_gender="f",
    )
    assert "[Verse]" in inp["prompt"]
    assert "female vocals" in inp["tags"]
    assert inp["negative_tags"] == "metal"
    assert inp["title"] == "Song"


def test_build_instrumental_input():
    inp = build_instrumental_input(tags="lofi, piano", title="Study")
    assert inp["make_instrumental"] is True
    assert inp["tags"] == "lofi, piano"


def test_build_continue_input():
    inp = build_continue_input(
        continue_clip_id="clip-1",
        continue_at=30,
        prompt="[Bridge]\nMore",
    )
    assert inp["continue_clip_id"] == "clip-1"
    assert inp["continue_at"] == 30
    assert inp["prompt"] == "[Bridge]\nMore"


def test_parse_lyrics_text():
    assert parse_lyrics_text({"text": "Line one"}) == "Line one"
    assert parse_lyrics_text({"result": [{"lyrics": "A B C"}]}) == "A B C"


def test_pick_track_second():
    tracks = [
        MusicTrack(audio_id="1", audio_url="https://a/1.mp3", variant_index=0),
        MusicTrack(audio_id="2", audio_url="https://a/2.mp3", variant_index=1),
    ]
    assert pick_track(tracks, "second").audio_id == "2"

from app.domain.entities.channel import Channel
from app.domain.value_objects.style_profile import StyleProfile


def test_channel_entity_with_style_profile():
    channel = Channel(
        owner_id=1,
        max_chat_id=100,
        title="Test Channel",
        topic="tech",
        style_profile=StyleProfile(tone="expert", audience="разработчики", topics=["python", "ai"]),
    )
    assert channel.topic == "tech"
    assert channel.style_profile.tone == "expert"
    assert channel.is_setup_complete is False
    assert channel.style_profile.to_dict()["tone"] == "expert"


def test_style_profile_defaults():
    profile = StyleProfile()
    assert profile.tone == ""
    assert profile.audience == ""
    assert profile.topics == []


def test_style_profile_from_dict():
    data = {
        "tone": "friendly",
        "audience": "студенты",
        "topics": ["обучение", "карьера"],
        "avg_length": 300,
    }
    profile = StyleProfile.from_dict(data)
    assert profile.tone == "friendly"
    assert profile.audience == "студенты"
    assert profile.avg_length == 300


def test_channel_setup_complete():
    channel = Channel(owner_id=1, max_chat_id=200, title="Ready")
    channel.is_setup_complete = True
    assert channel.is_setup_complete is True

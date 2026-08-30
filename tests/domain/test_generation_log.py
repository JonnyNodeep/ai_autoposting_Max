from app.domain.entities.generation_log import GenerationLog
from app.application.admin.cost_tracker import estimate_cost, MODEL_COSTS
from app.application.admin.billing_context import (
    billing_user,
    get_billing_user_id,
)


def test_generation_log_defaults():
    log = GenerationLog(user_id=1)
    assert log.user_id == 1
    assert log.tokens_used == 0
    assert log.estimated_cost == 0.0


def test_generation_log_fields():
    log = GenerationLog(
        user_id=5,
        channel_id=10,
        operation="generate_post",
        tokens_used=1500,
        model="gpt-5.5-mini",
        estimated_cost=0.012,
    )
    assert log.operation == "generate_post"
    assert log.tokens_used == 1500
    assert log.channel_id == 10


def test_estimate_cost_text_model():
    cost = estimate_cost("gpt-5.5-mini", 1_000_000)
    expected = (0.15 + 0.60) / 2
    assert abs(cost - expected) < 0.01


def test_estimate_cost_image_model():
    cost = estimate_cost("imagen-1.5", 0, "image", image_quality="medium")
    assert cost == 0.04


def test_estimate_cost_image_quality_high():
    cost = estimate_cost("imagen-1.5", 0, "image", image_quality="high")
    assert cost == 0.08


def test_estimate_cost_tts():
    cost = estimate_cost("gpt-4o-mini-tts", 0, "tts", chars=1_000_000)
    assert abs(cost - 12.0) < 0.01


def test_estimate_cost_unknown_text_uses_default():
    cost = estimate_cost("unknown-model", 1_000_000)
    expected = (0.15 + 0.60) / 2
    assert abs(cost - expected) < 0.01


def test_model_costs_defined():
    assert "gpt-5.5-mini" in MODEL_COSTS
    assert "imagen-1.5" in MODEL_COSTS
    assert "gpt-4o-mini-tts" in MODEL_COSTS


def test_billing_user_context():
    assert get_billing_user_id() is None
    with billing_user(42):
        assert get_billing_user_id() == 42
    assert get_billing_user_id() is None


def test_billing_user_none_noop():
    with billing_user(None):
        assert get_billing_user_id() is None

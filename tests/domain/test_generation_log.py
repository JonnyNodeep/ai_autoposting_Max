from app.domain.entities.generation_log import GenerationLog
from app.application.admin.cost_tracker import estimate_cost, MODEL_COSTS


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
    cost = estimate_cost("imagen-1.5", 0, "generate_image")
    assert cost == 0.04


def test_estimate_cost_unknown_model():
    cost = estimate_cost("unknown-model", 1000)
    assert cost == 0.0


def test_model_costs_defined():
    assert "gpt-5.5-mini" in MODEL_COSTS
    assert "imagen-1.5" in MODEL_COSTS

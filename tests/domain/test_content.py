from app.domain.entities.content_plan import ContentPlan, PlanStatus
from app.domain.entities.content_topic import ContentTopic, TopicStatus
from app.domain.entities.content_post import ContentPost, PostStatus


def test_content_plan_defaults():
    plan = ContentPlan(channel_id=1, duration_days=7)
    assert plan.channel_id == 1
    assert plan.duration_days == 7
    assert plan.status == PlanStatus.DRAFT


def test_content_topic_defaults():
    topic = ContentTopic(plan_id=1, topic="Тестовая тема")
    assert topic.plan_id == 1
    assert topic.topic == "Тестовая тема"
    assert topic.status == TopicStatus.PENDING
    assert topic.is_ai_generated is True
    assert topic.order == 0


def test_content_post_defaults():
    post = ContentPost(topic_id=1)
    assert post.topic_id == 1
    assert post.status == PostStatus.DRAFT
    assert post.title == ""


def test_content_post_fields():
    post = ContentPost(
        topic_id=1,
        title="Заголовок",
        text="Текст поста",
        cta="Подпишись!",
        image_prompt="A cat typing",
        image_url="https://example.com/cat.png",
        status=PostStatus.READY,
    )
    assert post.title == "Заголовок"
    assert post.status == PostStatus.READY
    assert post.image_url == "https://example.com/cat.png"


def test_plan_status_transitions():
    plan = ContentPlan(channel_id=1, duration_days=7)
    assert plan.status == PlanStatus.DRAFT
    plan.status = PlanStatus.APPROVED
    assert plan.status == PlanStatus.APPROVED
    plan.status = PlanStatus.IN_PROGRESS
    assert plan.status == PlanStatus.IN_PROGRESS
    plan.status = PlanStatus.COMPLETED
    assert plan.status == PlanStatus.COMPLETED


def test_topic_status_transitions():
    topic = ContentTopic(plan_id=1, topic="Test")
    topic.status = TopicStatus.APPROVED
    assert topic.status == TopicStatus.APPROVED
    topic.status = TopicStatus.SKIPPED
    assert topic.status == TopicStatus.SKIPPED

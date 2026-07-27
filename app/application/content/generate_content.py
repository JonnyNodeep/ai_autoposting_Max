import json
import re
from datetime import datetime, UTC, timedelta

from loguru import logger

from app.domain.entities.content_plan import ContentPlan, PlanStatus
from app.domain.entities.content_topic import ContentTopic, TopicStatus
from app.domain.entities.content_post import ContentPost, PostStatus
from app.domain.interfaces.channel_repository import ChannelRepository
from app.domain.interfaces.content_repository import (
    ContentPlanRepository,
    ContentTopicRepository,
    ContentPostRepository,
)
from app.domain.interfaces.max_client import MaxAPIClient
from app.domain.interfaces.openai_client import OpenAIClient
from app.application.content.prompts import ContentPrompts


class GenerateTopicsUseCase:
    def __init__(
        self,
        channel_repo: ChannelRepository,
        openai_client: OpenAIClient,
    ) -> None:
        self._channel_repo = channel_repo
        self._openai = openai_client

    async def execute(self, channel_id: int, duration_days: int, user_prefs: str | None = None,
                      post_settings: dict | None = None) -> list[str]:
        channel = await self._channel_repo.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        freq = (post_settings or {}).get("frequency") or channel.content_frequency or "daily"
        slots_per_day = {"2x_day": 2, "3x_day": 3}.get(freq, 1)
        topic_count = duration_days * slots_per_day

        search_results = ""
        if post_settings and post_settings.get("search_enabled"):
            topics = channel.style_profile.topics or [channel.topic or channel.title]
            query = f"найди актуальную информацию по темам: {', '.join(topics[:5])}"
            search_results = await self._openai.search_web(query)

        system, user = ContentPrompts.generate_topics(
            title=channel.title,
            topic=channel.topic or "общие темы",
            style_profile=channel.style_profile.to_dict(),
            duration_days=duration_days,
            topic_count=topic_count,
            user_prefs=user_prefs,
            search_results=search_results,
        )

        response = await self._openai.generate_text(prompt=user, system_prompt=system)
        lines = [line.strip() for line in response.strip().split("\n") if line.strip()]
        lines = [l.lstrip("-•*0123456789. ") for l in lines]

        logger.info(f"Generated {len(lines)} topics for channel {channel_id}")
        return lines[:topic_count]


class CreateContentPlanUseCase:
    def __init__(
        self,
        plan_repo: ContentPlanRepository,
        topic_repo: ContentTopicRepository,
        channel_repo: ChannelRepository,
        openai_client: OpenAIClient,
    ) -> None:
        self._plan_repo = plan_repo
        self._topic_repo = topic_repo
        self._channel_repo = channel_repo
        self._openai = openai_client

    async def execute(self, channel_id: int, duration_days: int, user_prefs: str | None = None, post_settings: dict | None = None) -> ContentPlan:
        generate_uc = GenerateTopicsUseCase(self._channel_repo, self._openai)
        topic_lines = await generate_uc.execute(channel_id, duration_days, user_prefs, post_settings)

        plan = await self._plan_repo.create(
            ContentPlan(channel_id=channel_id, duration_days=duration_days, post_settings=post_settings or {})
        )

        today = datetime.now(UTC).date()
        topics = []
        for i, line in enumerate(topic_lines):
            date_offset = (duration_days // max(len(topic_lines), 1)) * i
            scheduled = (today + timedelta(days=min(date_offset, duration_days - 1))).isoformat()
            topics.append(
                ContentTopic(
                    plan_id=plan.id,
                    topic=line,
                    scheduled_date=scheduled,
                    order=i,
                    is_ai_generated=True,
                    status=TopicStatus.PENDING,
                )
            )

        await self._topic_repo.create_batch(topics)
        logger.info(f"Content plan created: plan_id={plan.id} topics={len(topics)}")
        return plan


class GeneratePostUseCase:
    def __init__(
        self,
        plan_repo: ContentPlanRepository,
        channel_repo: ChannelRepository,
        post_repo: ContentPostRepository,
        topic_repo: ContentTopicRepository,
        openai_client: OpenAIClient,
    ) -> None:
        self._plan_repo = plan_repo
        self._channel_repo = channel_repo
        self._post_repo = post_repo
        self._topic_repo = topic_repo
        self._openai = openai_client

    async def execute(self, topic_id: int) -> ContentPost:
        topic = await self._topic_repo.get_by_id(topic_id)
        if not topic:
            raise ValueError(f"Topic {topic_id} not found")

        plan = await self._plan_repo.get_by_id(topic.plan_id)
        channel = await self._channel_repo.get_by_id(plan.channel_id) if plan else None

        if not channel:
            raise ValueError(f"Channel not found for topic {topic_id}")

        search_results: str | None = None
        post_settings = plan.post_settings if plan else None
        if post_settings and post_settings.get("search_enabled"):
            search_results = await self._openai.search_web(topic.topic)

        system, user = ContentPrompts.generate_post(
            title=channel.title,
            topic_text=topic.topic,
            style_profile=channel.style_profile.to_dict(),
            sample_posts=channel.sample_posts,
            post_settings=post_settings,
            channel_link=channel.channel_link if plan and plan.post_settings else "",
            search_results=search_results,
            reference_post=channel.style_profile.reference_post if channel and channel.style_profile else "",
            user_prefs=post_settings.get("user_prefs", "") if post_settings else "",
        )

        response = await self._openai.generate_text(prompt=user, system_prompt=system)

        cleaned = response.strip()
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start != -1 and end > start:
            cleaned = cleaned[start:end + 1]

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = {
                "title": topic.topic,
                "text": response,
                "cta": "Подписывайся, чтобы не пропустить новое!",
                "image_prompt": f"Illustration for: {topic.topic}",
            }

        post = await self._post_repo.create(
            ContentPost(
                topic_id=topic_id,
                title=data.get("title", topic.topic)[:256],
                text=data.get("text", response)[:4000],
                cta=data.get("cta", ""),
                image_prompt=data.get("image_prompt", ""),
                status=PostStatus.READY,
            )
        )

        subscribe_url = channel.channel_link if plan and plan.post_settings and plan.post_settings.get("subscribe_cta") else ""
        if subscribe_url:
            if "Подпишись на канал" not in post.text and "Подпишитесь на канал" not in post.text:
                post.text = post.text[:3900] + f"\n\n👉 [Подпишись на канал]({subscribe_url})"
                await self._post_repo.update(post)

        topic.status = TopicStatus.APPROVED
        await self._topic_repo.update(topic)

        logger.info(f"Post generated for topic {topic_id}: {post.title[:50]}")
        return post


class GenerateImageForPostUseCase:
    def __init__(
        self,
        post_repo: ContentPostRepository,
        openai_client: OpenAIClient,
        max_client: MaxAPIClient | None = None,
    ) -> None:
        self._post_repo = post_repo
        self._openai = openai_client
        self._max_client = max_client

    async def execute(self, post_id: int, channel_link: str | None = None) -> str:
        post = await self._post_repo.get_by_id(post_id)
        if not post:
            raise ValueError(f"Post {post_id} not found")

        result = await self._openai.generate_image(post.image_prompt, channel_link)

        if result.startswith("http://") or result.startswith("https://"):
            post.image_url = result
        elif self._max_client:
            token = await self._max_client.upload_file(result, "image")
            post.image_url = token
        else:
            post.image_url = result

        await self._post_repo.update(post)

        logger.info(f"Image generated for post {post_id}")
        return post.image_url or ""


class PublishPostUseCase:
    def __init__(
        self,
        post_repo: ContentPostRepository,
        max_client: MaxAPIClient,
    ) -> None:
        self._post_repo = post_repo
        self._max_client = max_client

    async def execute(self, post_id: int, chat_id: int) -> dict:
        post = await self._post_repo.get_by_id(post_id)
        if not post:
            raise ValueError(f"Post {post_id} not found")

        membership = await self._max_client.get_chat_members_me(chat_id)
        if not membership.get("is_admin", False):
            raise ValueError("Bot is not an admin in this channel")

        text = f"*{post.title}*\n\n{post.text}"
        if post.cta and post.cta not in post.text:
            text += f"\n\n{post.cta}"
        attachments = []
        if post.image_url:
            payload = {"token": post.image_url} if "/app/uploads/" not in (post.image_url or "") else {"url": post.image_url}
            attachments.append({"type": "image", "payload": payload})

        result = await self._max_client.send_message(
            chat_id=chat_id,
            text=text[:4000],
            attachments=attachments if attachments else None,
            fmt="markdown",
        )

        post.status = PostStatus.PUBLISHED
        await self._post_repo.update(post)

        logger.info(f"Post {post_id} published to chat {chat_id}")
        return result


class EditPostUseCase:
    def __init__(
        self,
        post_repo: ContentPostRepository,
        openai_client: OpenAIClient,
    ) -> None:
        self._post_repo = post_repo
        self._openai = openai_client

    async def execute(self, post_id: int, edit_type: str, style_profile: dict | None = None,
                      custom_instruction: str | None = None) -> ContentPost:
        post = await self._post_repo.get_by_id(post_id)
        if not post:
            raise ValueError(f"Post {post_id} not found")

        system, user = ContentPrompts.edit_post(
            title=post.title,
            text=post.text,
            cta=post.cta,
            edit_type=edit_type,
            style_profile=style_profile,
            custom_instruction=custom_instruction,
        )

        response = await self._openai.generate_text(prompt=user, system_prompt=system)

        cleaned = response.strip()
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start != -1 and end > start:
            cleaned = cleaned[start:end + 1]

        try:
            data = json.loads(cleaned)
            post.title = data.get("title", post.title)[:256]
            post.text = data.get("text", post.text)[:4000]
            post.cta = data.get("cta", post.cta)[:512]
        except json.JSONDecodeError:
            pass

        await self._post_repo.update(post)
        logger.info(f"Post {post_id} edited with '{edit_type}'")
        return post

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceProfile:
    profile_id: str
    label: str
    topic_kinds: tuple[str, ...]
    trust_weight: float
    ttl_seconds: int
    query_template: str
    notes: str
    allow_domains: tuple[str, ...] = ()
    deny_domains: tuple[str, ...] = ()
    credibility_class: str = "curated"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_PROFILES: dict[str, SourceProfile] = {
    "official_docs": SourceProfile(
        profile_id="official_docs",
        label="Official docs",
        topic_kinds=("technical", "design", "integration"),
        trust_weight=0.82,
        ttl_seconds=60 * 60 * 24 * 21,
        query_template="{topic} official docs site:docs.python.org OR site:developer.mozilla.org OR site:web.dev OR site:developer.android.com OR site:developer.apple.com",
        notes="Best for stable technical guidance and standards.",
        allow_domains=("docs.python.org", "developer.mozilla.org", "web.dev", "developer.android.com", "developer.apple.com"),
        credibility_class="primary_technical",
    ),
    "messaging_platform_docs": SourceProfile(
        profile_id="messaging_platform_docs",
        label="Messaging platform docs",
        topic_kinds=("integration", "technical"),
        trust_weight=0.84,
        ttl_seconds=60 * 60 * 24 * 21,
        query_template="{topic} site:core.telegram.org OR site:discord.com/developers",
        notes="Focused on Telegram and Discord bot/platform documentation.",
        allow_domains=("core.telegram.org", "discord.com", "discord.com/developers"),
        credibility_class="primary_platform",
    ),
    "reputable_repos": SourceProfile(
        profile_id="reputable_repos",
        label="Reputable repositories",
        topic_kinds=("technical", "integration", "design"),
        trust_weight=0.68,
        ttl_seconds=60 * 60 * 24 * 14,
        query_template="{topic} site:github.com",
        notes="Useful for current implementation patterns and examples.",
        allow_domains=("github.com",),
        credibility_class="repo_reference",
    ),
    "wikipedia_orientation": SourceProfile(
        profile_id="wikipedia_orientation",
        label="Wikipedia orientation",
        topic_kinds=("technical", "design", "news", "general"),
        trust_weight=0.54,
        ttl_seconds=60 * 60 * 24 * 7,
        query_template="{topic} site:wikipedia.org",
        notes="Good for fast orientation, not canonical truth by itself.",
        allow_domains=("wikipedia.org",),
        credibility_class="orientation",
    ),
    "product_design": SourceProfile(
        profile_id="product_design",
        label="Design guidance",
        topic_kinds=("design",),
        trust_weight=0.72,
        ttl_seconds=60 * 60 * 24 * 14,
        query_template="{topic} site:material.io OR site:developer.apple.com/design OR site:web.dev",
        notes="Design-system and interaction guidance.",
        allow_domains=("material.io", "developer.apple.com", "web.dev"),
        credibility_class="design_guidance",
    ),
    "reputable_news": SourceProfile(
        profile_id="reputable_news",
        label="Reputable news",
        topic_kinds=("news",),
        trust_weight=0.52,
        ttl_seconds=60 * 60 * 12,
        query_template="{topic} site:reuters.com OR site:apnews.com OR site:bbc.com OR site:cnn.com",
        notes="Short-lived pulse on current events. Must decay quickly.",
        allow_domains=("reuters.com", "apnews.com", "bbc.com", "cnn.com"),
        deny_domains=("rt.com", "sputniknews.com", "infowars.com", "oann.com", "thegatewaypundit.com", "breitbart.com", "newsmax.com"),
        credibility_class="reputable_news",
    ),
}


def get_source_profile(profile_id: str) -> SourceProfile | None:
    return _PROFILES.get(profile_id)


def profiles_for_topic(topic_kind: str, topic: str) -> list[SourceProfile]:
    topic_kind = (topic_kind or "general").strip().lower() or "general"
    lowered = (topic or "").lower()
    selected: list[SourceProfile] = []
    platform_topic = any(token in lowered for token in ("telegram", "discord", "bot", "api", "webhook"))

    if topic_kind in {"technical", "integration"}:
        if platform_topic:
            selected.extend(
                [
                    _PROFILES["messaging_platform_docs"],
                    _PROFILES["reputable_repos"],
                    _PROFILES["official_docs"],
                    _PROFILES["wikipedia_orientation"],
                ]
            )
        else:
            selected.extend(
                [
                    _PROFILES["official_docs"],
                    _PROFILES["reputable_repos"],
                    _PROFILES["wikipedia_orientation"],
                ]
            )
    elif topic_kind == "design":
        selected.extend(
            [
                _PROFILES["product_design"],
                _PROFILES["official_docs"],
                _PROFILES["reputable_repos"],
            ]
        )
    elif topic_kind == "news":
        selected.extend(
            [
                _PROFILES["reputable_news"],
                _PROFILES["wikipedia_orientation"],
            ]
        )
    else:
        selected.extend(
            [
                _PROFILES["official_docs"],
                _PROFILES["wikipedia_orientation"],
            ]
        )

    deduped: list[SourceProfile] = []
    seen: set[str] = set()
    for profile in selected:
        if profile.profile_id in seen:
            continue
        seen.add(profile.profile_id)
        deduped.append(profile)
    return deduped


def render_query(profile: SourceProfile, topic: str) -> str:
    lowered = topic.strip().lower()
    if profile.profile_id == "messaging_platform_docs":
        if "telegram" in lowered or "bot api" in lowered:
            return f"{topic.strip()} site:core.telegram.org"
        if "discord" in lowered:
            return f"{topic.strip()} site:discord.com/developers OR site:discord.com"
    return profile.query_template.format(topic=topic.strip())


def allowed_domains_for_topic(profile: SourceProfile, topic: str) -> tuple[str, ...]:
    lowered = topic.strip().lower()
    if profile.profile_id == "messaging_platform_docs":
        if "telegram" in lowered or "bot api" in lowered:
            return ("core.telegram.org",)
        if "discord" in lowered:
            return ("discord.com", "discord.com/developers")
    return profile.allow_domains

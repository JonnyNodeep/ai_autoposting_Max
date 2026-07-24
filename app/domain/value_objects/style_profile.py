from dataclasses import dataclass, field


@dataclass
class StyleProfile:
    tone: str = ""
    audience: str = ""
    topics: list[str] = field(default_factory=list)
    format_preference: str = "post"
    avg_length: int = 500
    features: list[str] = field(default_factory=list)
    visual_style: str = ""

    def to_dict(self) -> dict:
        return {
            "tone": self.tone,
            "audience": self.audience,
            "topics": self.topics,
            "format_preference": self.format_preference,
            "avg_length": self.avg_length,
            "features": self.features,
            "visual_style": self.visual_style,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StyleProfile":
        return cls(
            tone=data.get("tone", ""),
            audience=data.get("audience", ""),
            topics=data.get("topics", []),
            format_preference=data.get("format_preference", "post"),
            avg_length=data.get("avg_length", 500),
            features=data.get("features", []),
            visual_style=data.get("visual_style", ""),
        )

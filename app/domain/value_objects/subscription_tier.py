from enum import StrEnum


class SubscriptionTier(StrEnum):
    SOLO = "solo"
    CREATOR = "creator"
    STUDIO = "studio"

    @property
    def channels_limit(self) -> int:
        limits = {
            SubscriptionTier.SOLO: 1,
            SubscriptionTier.CREATOR: 3,
            SubscriptionTier.STUDIO: 10,
        }
        return limits[self]

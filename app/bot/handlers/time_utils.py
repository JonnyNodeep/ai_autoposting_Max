import re


def parse_time_hh_mm(text: str) -> tuple[int, int] | None:
    normalized = text.strip().replace(",", ".").replace("-", ":").replace(" ", ":")
    match = re.match(r"(\d{1,2})[:.](\d{2})", normalized)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23:
        return None
    if minute < 0 or minute > 59:
        return None
    return hour, minute

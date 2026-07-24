import sys


MIN_SUPPORTED_PYTHON = (3, 12)


def ensure_supported_python(min_version: tuple[int, int] = MIN_SUPPORTED_PYTHON) -> None:
    if sys.version_info >= min_version:
        return

    current_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    required_version = f"{min_version[0]}.{min_version[1]}"
    raise RuntimeError(
        "Unsupported Python version. "
        f"AI Content Studio requires Python >= {required_version}, got {current_version}. "
        "Use Poetry with Python 3.12 or run tests inside Docker."
    )

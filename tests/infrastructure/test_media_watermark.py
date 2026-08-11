from pathlib import Path

from PIL import Image

from app.infrastructure.services.media_watermark import apply_logo_image


def test_apply_logo_image_writes_dest_without_mutating_src(tmp_path: Path):
    src = tmp_path / "src.png"
    logo = tmp_path / "logo.png"
    dest = tmp_path / "out.png"
    Image.new("RGB", (100, 100), color=(0, 0, 50)).save(src)
    Image.new("RGBA", (20, 20), color=(255, 255, 0, 255)).save(logo)
    before = src.read_bytes()

    out = apply_logo_image(str(src), str(logo), str(dest))

    assert out == str(dest)
    assert dest.exists()
    assert src.read_bytes() == before
    assert dest.stat().st_size > 0
    assert dest.read_bytes() != before

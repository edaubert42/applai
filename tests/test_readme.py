"""The README's first screen: the opening sentence, the demo's GIF and the demo link (#264)."""

import re
import struct
from pathlib import Path

import demo_gif
import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
GIF = ROOT / "docs" / "demo.gif"
LINK = "[Demo (serverless: the first load takes a few seconds)](https://applai-production-5a4220b3.up.railway.app)"
# What GitHub shows without scrolling: the lines before the first table.
FIRST_SCREEN = README[: next(i for i, line in enumerate(README) if line.startswith("|"))]


def _gif(*delays_cs: int) -> bytes:
    """A GIF of 1x1 frames shown for the given times, in hundredths of a second."""
    data = b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0, 0, 0)
    for delay in delays_cs:
        data += b"\x21\xf9\x04\x00" + struct.pack("<H", delay) + b"\x00\x00"
        data += b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x4c\x01\x00"
    return data + b"\x3b"


def test_the_sentence_the_gif_and_the_demo_link_show_without_scrolling():
    text = "\n".join(FIRST_SCREEN)

    assert FIRST_SCREEN[0] == "# ApplAI"
    sentence = next(i for i, line in enumerate(FIRST_SCREEN) if line.startswith("Claude agents"))
    image = next(i for i, line in enumerate(FIRST_SCREEN) if "docs/demo.gif" in line)
    link = next(i for i, line in enumerate(FIRST_SCREEN) if LINK in line)
    assert sentence < image  # the GIF right under the sentence
    assert sentence < link
    assert len(FIRST_SCREEN) <= 12  # one screen: the table comes next
    assert re.search(r"!\[[^\]]+\]\(docs/demo\.gif\)", text)  # with an alt text


def test_the_gif_is_under_30_s_and_5_mb_and_the_repository_allows_it():
    data = GIF.read_bytes()

    assert data[:6] in (b"GIF87a", b"GIF89a")
    assert len(data) < 5 * 1024 * 1024
    assert 5 < demo_gif.gif_seconds(data) < 30
    demo_gif.check_gif(data)
    allowed = (ROOT / "tools" / "allowed_files.txt").read_text(encoding="utf-8").splitlines()
    assert "docs/demo.gif" in allowed


def test_gif_seconds_adds_the_time_each_frame_is_shown():
    assert demo_gif.gif_seconds(_gif(150, 50, 300)) == 5.0


def test_a_gif_over_30_s_or_5_mb_or_not_a_gif_is_refused_with_the_reason():
    with pytest.raises(demo_gif.GifError, match="30 s"):
        demo_gif.check_gif(_gif(2000, 1001))
    with pytest.raises(demo_gif.GifError, match="5 MB"):
        demo_gif.check_gif(_gif(100) + b"\x00" * (5 * 1024 * 1024))
    with pytest.raises(demo_gif.GifError, match="not a GIF"):
        demo_gif.check_gif(b"\x89PNG\r\n")
    demo_gif.check_gif(_gif(2000, 1000))  # exactly 30 s is allowed


def test_a_refused_gif_is_not_saved_and_the_previous_one_stays(tmp_path: Path):
    out = tmp_path / "demo.gif"
    previous = _gif(100, 100)

    demo_gif.save_gif(previous, out)
    with pytest.raises(demo_gif.GifError, match="30 s"):
        demo_gif.save_gif(_gif(4000), out)

    assert out.read_bytes() == previous
    demo_gif.save_gif(previous, out)  # saving again is safe
    assert out.read_bytes() == previous

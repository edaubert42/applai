"""Record the README's GIF on `applai demo`, so that it can be made again (#264).

Usage: uv run --with pillow python tools/demo_gif.py [OUT]   (default: docs/demo.gif)

Needs the built frontend (`npm run build` in frontend/) and Chrome. It starts `applai demo` on a
free port, drives it with Playwright on the fictional candidate (an offer triaged, the letter
drafted with its cited facts, the candidate's check, the timeline with models and costs), and
assembles the screenshots with Pillow, which is not an ApplAI dependency. A GIF over 30 s or 5 MB
is refused and OUT is left as it was.
"""

import importlib
import io
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx2
from playwright.sync_api import Page, ViewportSize, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "demo.gif"
MAX_SECONDS = 30
MAX_BYTES = 5 * 1024 * 1024
VIEWPORT = ViewportSize(width=960, height=620)
START_WITHIN = 30  # seconds the demo has to answer


class GifError(Exception):
    pass


def gif_seconds(data: bytes) -> float:
    """How long a GIF plays once: the sum of its frames' delays."""
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise GifError("not a GIF")
    position = 13
    if data[10] & 0x80:  # a global color table follows the screen descriptor
        position += 3 * 2 ** ((data[10] & 7) + 1)
    hundredths = 0
    while position < len(data) and data[position] != 0x3B:
        block = data[position]
        if block == 0x21:  # extension: label, then sub-blocks
            if data[position + 1] == 0xF9:
                hundredths += struct.unpack_from("<H", data, position + 4)[0]
            position += 2
        elif block == 0x2C:  # image: descriptor, optional color table, code size, sub-blocks
            flags = data[position + 9]
            position += 10
            if flags & 0x80:
                position += 3 * 2 ** ((flags & 7) + 1)
            position += 1
        else:
            raise GifError(f"not a GIF: unknown block at byte {position}")
        while data[position]:  # sub-blocks end with a block of length 0
            position += data[position] + 1
        position += 1
    return hundredths / 100


def check_gif(data: bytes) -> None:
    seconds = gif_seconds(data)
    if seconds > MAX_SECONDS:
        raise GifError(f"the GIF plays {seconds:.1f} s: at most {MAX_SECONDS} s")
    if len(data) >= MAX_BYTES:
        raise GifError(f"the GIF weighs {len(data) / 2**20:.1f} MB: under 5 MB")


def save_gif(data: bytes, out: Path) -> None:
    """Write the GIF if it is within the limits; otherwise raise and leave `out` as it was."""
    check_gif(data)
    out.write_bytes(data)


def _assemble(frames: list[tuple[bytes, float]]) -> bytes:
    # Pillow is not a dependency (uv run --with pillow), so it is not typed here.
    Image: Any = importlib.import_module("PIL.Image")

    images = [Image.open(io.BytesIO(png)).convert("RGB") for png, _ in frames]
    palette = [image.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for image in images]
    buffer = io.BytesIO()
    palette[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=palette[1:],
        duration=[round(seconds * 1000) for _, seconds in frames],
        loop=0,
        optimize=True,
    )
    return buffer.getvalue()


def _record(base: str) -> list[tuple[bytes, float]]:
    frames: list[tuple[bytes, float]] = []

    def shot(page: Page, seconds: float) -> None:
        time.sleep(0.4)  # the screen settles
        frames.append((page.screenshot(), seconds))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome")
        try:
            page = browser.new_page(viewport=VIEWPORT, color_scheme="dark", locale="fr-FR")
            page.goto(base)
            page.get_by_role("button", name="À revoir").click()
            page.get_by_text("Écartées").first.click()  # the offers Triage scored, and why
            page.get_by_text("Score 25").scroll_into_view_if_needed()
            shot(page, 5)
            page.get_by_role("button", name="Candidatures").click()
            page.get_by_role("button", name="Corriger").click()  # the letter drafted
            shot(page, 5)
            page.get_by_text("Faits cités").first.click()  # each sentence cites a fact
            shot(page, 6)
            page.get_by_role("button", name="Accepter").first.click()  # the candidate's check
            shot(page, 4)
            page.get_by_role("button", name="Retour").click()
            card = page.get_by_role("listitem").filter(has_text="Coquelicot Logistique")
            card.get_by_role("button", name="Trace").click()  # models and costs
            page.get_by_text("Total :").scroll_into_view_if_needed()
            shot(page, 7)
        finally:
            browser.close()
    return frames


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main(out: Path = OUT) -> int:
    port = _free_port()
    env = {name: value for name, value in os.environ.items() if not name.startswith("APPLAI_")}
    env["PORT"] = str(port)
    demo = subprocess.Popen(
        [sys.executable, "-m", "applai", "demo"],
        env=env,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        started = time.monotonic()
        with httpx2.Client() as http:
            while True:
                if demo.poll() is not None:
                    raise GifError("applai demo stopped before it answered")
                try:
                    http.get(f"{base}/api/applications").raise_for_status()
                    break
                except httpx2.HTTPError as error:
                    if time.monotonic() - started > START_WITHIN:
                        raise GifError(f"applai demo did not answer in {START_WITHIN} s") from error
                    time.sleep(0.2)
        save_gif(_assemble(_record(base)), out)
    except GifError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        demo.terminate()
        demo.communicate(timeout=10)
    print(f"{out}: {gif_seconds(out.read_bytes()):.0f} s, {out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT))

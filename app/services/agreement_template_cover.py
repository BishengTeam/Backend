"""Server-rendered cover thumbnails for agreement template versions."""

from __future__ import annotations

import asyncio
from html import escape
from typing import ClassVar

from playwright.async_api import async_playwright

from app.services.upload import UploadService


COVER_VIEWPORT_WIDTH = 720
COVER_VIEWPORT_HEIGHT = 960
COVER_DEVICE_SCALE_FACTOR = 2
COVER_JPEG_QUALITY = 90
COVER_GENERATION_TIMEOUT_SECONDS = 10.0


def build_agreement_cover_html(title: str, content: str) -> str:
    """Wrap rich agreement HTML in a deterministic, offline 3:4 layout."""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      width: 720px;
      height: 960px;
      margin: 0;
      overflow: hidden;
      background: #ffffff;
      color: #1f2937;
      font-family: 'Noto Sans CJK SC', 'Noto Sans SC', 'PingFang SC',
        'Microsoft YaHei', sans-serif;
      -webkit-font-smoothing: antialiased;
    }}
    main {{
      height: 100%;
      padding: 64px 56px;
      overflow: hidden;
    }}
    h1 {{
      margin: 0 0 28px;
      font-size: 40px;
      line-height: 1.25;
      font-weight: 700;
      color: #111827;
      overflow-wrap: anywhere;
    }}
    .content {{
      font-size: 20px;
      line-height: 1.8;
      overflow-wrap: anywhere;
    }}
    .content img {{ max-width: 100%; height: auto; }}
    .content table {{ max-width: 100%; }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(title)}</h1>
    <section class="content">{content}</section>
  </main>
</body>
</html>"""


class AgreementCoverService:
    """Render the first screen of an agreement as a fixed-ratio JPEG."""

    _render_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    async def generate(self, title: str, content: str) -> str:
        """Render one cover and return its public media URL.

        Only one render is allowed per process. A standalone backfill process
        can still run concurrently with the API, so database updates must be
        conditional and callers must never overwrite an existing cover_url.
        """

        async with self._render_lock:
            image = await asyncio.wait_for(
                self._render(title, content),
                timeout=COVER_GENERATION_TIMEOUT_SECONDS,
            )
            if not image:
                raise RuntimeError("agreement cover renderer returned empty image")
            return str(UploadService.save_bytes(image, ".jpg")["url"])

    async def _render(self, title: str, content: str) -> bytes:
        html = build_agreement_cover_html(title, content)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(timeout=10_000)
            context = None
            try:
                context = await browser.new_context(
                    viewport={
                        "width": COVER_VIEWPORT_WIDTH,
                        "height": COVER_VIEWPORT_HEIGHT,
                    },
                    device_scale_factor=COVER_DEVICE_SCALE_FACTOR,
                    java_script_enabled=False,
                )
                page = await context.new_page()
                page.set_default_timeout(10_000)

                async def block_external_resources(route) -> None:
                    await route.abort()

                await page.route("**/*", block_external_resources)
                await page.set_content(html, wait_until="domcontentloaded")
                return await page.screenshot(
                    type="jpeg",
                    quality=COVER_JPEG_QUALITY,
                    full_page=False,
                    animations="disabled",
                )
            finally:
                if context is not None:
                    await context.close()
                await browser.close()

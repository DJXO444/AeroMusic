# Licensed under the MIT License.
# This file is part of AeroXMusic


import io
import os
import re
import asyncio

import aiohttp
from PIL import (Image, ImageDraw, ImageFilter, ImageFont, ImageOps)

from aero.helpers import Track

ASSETS = "aero/helpers"
BG_PATH = f"{ASSETS}/red.jpg"
FALLBACK = f"{ASSETS}/aero.jpg"
ICONS_PATH = f"{ASSETS}/play_icons.png"
FONT_BOLD = f"{ASSETS}/Poppins-SemiBold.ttf"
FONT_REG = f"{ASSETS}/Poppins-Regular.ttf"

CANVAS = (1280, 720)
THUMB_SIZE = (460, 460)
RADIUS = 42
PROGRESS = 0.34

_UID_RE = re.compile(r"tg://user\?id=(\d+)")


class Thumbnail:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def _fetch_image(self, url: str) -> Image.Image | None:
        if not url:
            return None
        try:
            async with self.session.get(url) as resp:
                data = await resp.read()
            return self._trim_bars(Image.open(io.BytesIO(data)).convert("RGB"))
        except Exception:
            return None

    async def _fetch_pfp(self, user_field: str | None) -> Image.Image | None:
        if not user_field:
            return None
        match = _UID_RE.search(user_field)
        if not match:
            return None
        try:
            from aero import app

            async for photo in app.get_chat_photos(int(match.group(1)), limit=1):
                data = await app.download_media(photo.file_id, in_memory=True)
                return Image.open(data).convert("RGB")
        except Exception:
            pass
        return None

    @staticmethod
    def _trim_bars(img: Image.Image, thresh: int = 24) -> Image.Image:
        gray = img.convert("L")
        w, h = gray.size
        px = gray.load()
        top, bottom = 0, h
        for y in range(h):
            if max(px[x, y] for x in range(0, w, 16)) > thresh:
                top = y
                break
        for y in range(h - 1, -1, -1):
            if max(px[x, y] for x in range(0, w, 16)) > thresh:
                bottom = y + 1
                break
        if bottom - top > h // 3:
            img = img.crop((0, top, w, bottom))
        return img

    @staticmethod
    def _fmt_time(sec) -> str:
        sec = int(sec or 0)
        return f"{sec // 60}:{sec % 60:02d}"

    @staticmethod
    def cleanup(path) -> None:
        """Delete a generated thumbnail from disk after it's been sent, so the
        cache never grows. Only touches our own cache/*.png files."""
        if isinstance(path, str) and path.startswith("cache/"):
            try:
                os.remove(path)
            except Exception:
                pass

    def _build_card(
        self,
        thumb: Image.Image,
        title: str,
        duration: int,
        pfp: Image.Image | None,
        size: tuple[int, int],
    ) -> Image.Image:
        canvas = Image.open(BG_PATH).convert("RGB").resize(size).convert("RGBA")

        # ---- left thumbnail card + shadow
        tw, th = THUMB_SIZE
        tx, ty = 110, (size[1] - th) // 2
        fitted = ImageOps.fit(thumb, (tw, th), Image.LANCZOS)
        mask = Image.new("L", (tw, th), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, tw, th], radius=RADIUS, fill=255)

        sh = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            [tx + 10, ty + 16, tx + tw + 10, ty + th + 16],
            radius=RADIUS, fill=(0, 0, 0, 150),
        )
        canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(22)))

        card = Image.new("RGBA", (tw, th))
        card.paste(fitted, (0, 0), mask)
        canvas.alpha_composite(card, (tx, ty))

        # ---- right panel geometry
        rx = tx + tw + 70
        rw = size[0] - rx - 90
        f_title = ImageFont.truetype(FONT_BOLD, 44)
        f_small = ImageFont.truetype(FONT_REG, 24)

        # ---- title (max 2 lines) with glow
        d = ImageDraw.Draw(canvas)
        words, lines, cur = title.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if d.textlength(t, font=f_title) <= rw - 95:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) > 2:
            lines = lines[:2]
            lines[1] = lines[1][:24].rstrip() + "…"

        title_y = 235
        glow = Image.new("RGBA", size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        yy = title_y
        for ln in lines:
            gd.text((rx, yy), ln, font=f_title, fill=(255, 215, 215, 255))
            yy += 58
        canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(12)))
        canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(4)))
        d = ImageDraw.Draw(canvas)
        yy = title_y
        for ln in lines:
            d.text((rx, yy), ln, font=f_title, fill=(255, 255, 255, 255))
            yy += 58
        title_bottom = yy

        # ---- pfp box (right of the title) with tiny white border
        if pfp is not None:
            p = 96
            prad = 14
            pfp_fit = ImageOps.fit(pfp, (p * 4, p * 4), Image.LANCZOS)
            pm = Image.new("L", (p * 4, p * 4), 0)
            ImageDraw.Draw(pm).rounded_rectangle(
                [0, 0, p * 4, p * 4], radius=prad * 4, fill=255
            )
            pfp_fit = pfp_fit.resize((p, p), Image.LANCZOS)
            pm = pm.resize((p, p), Image.LANCZOS)
            px_, py_ = rx + rw - p, title_y - 6
            canvas.paste(pfp_fit, (px_, py_), pm)
            d.rounded_rectangle(
                [px_ - 2, py_ - 2, px_ + p + 2, py_ + p + 2],
                radius=prad + 2, outline=(255, 255, 255, 255), width=3,
            )

        # ---- progress bar + timers
        by = title_bottom + 48
        bh = 8
        d.rounded_rectangle([rx, by, rx + rw, by + bh], radius=4, fill=(255, 255, 255, 70))
        d.rounded_rectangle(
            [rx, by, rx + int(rw * PROGRESS), by + bh],
            radius=4, fill=(255, 255, 255, 255),
        )
        kx = rx + int(rw * PROGRESS)
        d.ellipse(
            [kx - 9, by + bh // 2 - 9, kx + 9, by + bh // 2 + 9],
            fill=(255, 255, 255, 255),
        )
        elapsed = self._fmt_time((duration or 0) * PROGRESS)
        total = self._fmt_time(duration)
        d.text((rx, by + 20), elapsed, font=f_small, fill=(255, 255, 255, 200))
        tl = d.textlength(total, font=f_small)
        d.text((rx + rw - tl, by + 20), total, font=f_small, fill=(255, 255, 255, 200))

        # ---- player icons (image)
        iy = by + 112
        icons = Image.open(ICONS_PATH).convert("RGBA")
        iw, ih = icons.size
        target_w = rw
        target_h = int(ih * target_w / iw)
        icons = icons.resize((target_w, target_h), Image.LANCZOS)
        canvas.alpha_composite(icons, (rx, iy - target_h // 2))

        return canvas.convert("RGB")

    async def generate(self, song: Track, size=CANVAS) -> str:
        try:
            output = f"cache/{song.id}.png"
            if os.path.exists(output):
                return output

            thumb = await self._fetch_image(song.thumbnail)
            if thumb is None:
                return FALLBACK

            pfp = await self._fetch_pfp(song.user)

            loop = asyncio.get_event_loop()
            card = await loop.run_in_executor(
                None,
                self._build_card,
                thumb,
                song.title or "",
                song.duration_sec,
                pfp,
                size,
            )
            card.save(output)
            return output
        except Exception:
            return FALLBACK

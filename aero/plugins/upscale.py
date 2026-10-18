# Licensed under the MIT License.
# This file is part of AeroXMusic


import os
import random
from io import BytesIO

import aiohttp
from PIL import Image
from pyrogram import filters, types

from aero import app, lang, logger

_upscaling = set()


def _rand() -> int:
    return random.randint(1_000_000, 999_999_999_999)


async def _upscale(buffer: bytes, anime: bool = False, level: str = None) -> str | None:
    try:
        with Image.open(BytesIO(buffer)) as img:
            w, h = img.size

        form = aiohttp.FormData()
        form.add_field("image_file", buffer, filename="image.jpg", content_type="image/jpeg")
        form.add_field("name", str(_rand()))
        form.add_field("desiredHeight", str(h * 4))
        form.add_field("desiredWidth", str(w * 4))
        form.add_field("outputFormat", "png")
        if level:
            form.add_field("compressionLevel", level)
        form.add_field("anime", str(anime).lower())

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://upscalepics.com",
            "Referer": "https://upscalepics.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        logger.info("API → upscale: %dx%d → %dx%d (anime=%s)", w, h, w * 4, h * 4, anime)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.upscalepics.com/upscale-to-size",
                data=form,
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info("API ← upscale: ok")
                    return data.get("bgRemoved", "").strip()
                logger.warning("Upscale API HTTP %s", resp.status)
                return None
    except Exception as ex:
        logger.warning("Upscale request failed: %s", ex)
        return None


@app.on_message(filters.command(["upscale"]) & ~filters.forwarded & ~app.bl_users, group=11)
@lang.language()
async def upscale_cmd(_, m: types.Message):
    user_id = m.from_user.id

    if user_id in _upscaling:
        return await m.reply_text(m.lang["upscale_busy"])

    if not m.reply_to_message or not m.reply_to_message.photo:
        return await m.reply_text(m.lang["upscale_reply"])

    _upscaling.add(user_id)
    sent = await m.reply_text(m.lang["upscale_processing"])

    path = None
    out_path = None

    try:
        args = m.text.split(maxsplit=1)[1] if len(m.command) > 1 else ""
        anime = "anime" in args
        level = args.split("lvl-")[1] if "lvl-" in args else None

        path = await m.reply_to_message.download()
        with open(path, "rb") as f:
            buf = f.read()

        image_url = await _upscale(buf, anime=anime, level=level)

        if image_url and image_url.startswith("http"):
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        out_path = f"cache/upscaled_{m.id}.png"
                        with open(out_path, "wb") as f:
                            f.write(content)

                        await m.reply_document(
                            out_path,
                            caption=m.lang["upscale_caption"].format(app.username),
                        )
                    else:
                        raise Exception
        else:
            raise Exception
    except Exception:
        await m.reply_text(m.lang["upscale_failed"])
    finally:
        for p in (path, out_path):
            if p and os.path.exists(p):
                os.remove(p)
        await sent.delete()
        _upscaling.discard(user_id)
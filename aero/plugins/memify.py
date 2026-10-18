# This file is part of AeroXMusic
# Licensed under the MIT License. See the LICENSE file for details.


import asyncio
import textwrap
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from pyrogram import filters, types

from aero import app, lang, logger

FONT_PATH = "aero/helpers/Poppins-SemiBold.ttf"
WRAP_WIDTH = 15


def _draw_outlined(draw, xy, text, font):
    x, y = xy
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=(255, 255, 255))


def _render(data: bytes, text: str) -> BytesIO | None:
    try:
        img = Image.open(BytesIO(data)).convert("RGBA")
    except Exception as ex:
        logger.warning("Memify: cannot open image: %s", ex)
        return None

    i_width, i_height = img.size
    font = ImageFont.truetype(FONT_PATH, max(int((70 / 640) * i_width), 12))

    upper, _, lower = text.partition(";")
    upper, lower = upper.strip(), lower.strip()

    draw = ImageDraw.Draw(img)
    pad = 5

    current_h = int((10 / 640) * i_width)
    for line in textwrap.wrap(upper, width=WRAP_WIDTH):
        l, t, r, b = font.getbbox(line)
        w, h = r - l, b - t
        _draw_outlined(draw, ((i_width - w) / 2, current_h), line, font)
        current_h += h + pad

    if lower:
        lines = textwrap.wrap(lower, width=WRAP_WIDTH)
        bottom = i_height - int((20 / 640) * i_width)
        for line in reversed(lines):
            l, t, r, b = font.getbbox(line)
            w, h = r - l, b - t
            bottom -= h + pad
            _draw_outlined(draw, ((i_width - w) / 2, bottom), line, font)

    out = BytesIO()
    out.name = "memify.webp"
    img.save(out, "webp")
    out.seek(0)
    return out


@app.on_message(filters.command("mmf") & ~app.bl_users)
@lang.language()
async def memify(_, m: types.Message):
    reply = m.reply_to_message
    if not reply or not (reply.photo or reply.sticker or reply.document):
        return await m.reply_text(m.lang["mmf_reply"])
    if len(m.command) < 2:
        return await m.reply_text(m.lang["mmf_usage"].format(m.command[0]))

    text = m.text.split(maxsplit=1)[1].strip()
    if not text:
        return await m.reply_text(m.lang["mmf_usage"].format(m.command[0]))

    if getattr(reply.sticker, "is_animated", False) or getattr(
        reply.sticker, "is_video", False
    ):
        return await m.reply_text(m.lang["mmf_reply"])

    sent = await m.reply_text(m.lang["mmf_processing"])
    src = None
    result = None
    try:
        src = await app.download_media(reply, in_memory=True)
        data = bytes(src.getbuffer())
        result = await asyncio.to_thread(_render, data, text)
        if not result:
            return await sent.edit_text(m.lang["mmf_failed"])
        await m.reply_document(result)
    except Exception as ex:
        logger.warning("Memify failed: %s", ex)
        await sent.edit_text(m.lang["mmf_failed"])
        return
    finally:
        if src is not None:
            src.close()
        if result is not None:
            result.close()

    try:
        await sent.delete()
    except Exception:
        pass

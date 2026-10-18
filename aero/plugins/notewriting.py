# This file is part of AeroXMusic
# Licensed under the MIT License. See the LICENSE file for details.


from io import BytesIO
from urllib.parse import quote

import aiohttp
from pyrogram import filters, types

from aero import app, lang, logger

API = "https://apis.xditya.me/write"
MAX_CHARS = 256
_IMG_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF")


@app.on_message(filters.command("write") & ~app.bl_users)
@lang.language()
async def handwrite(_, m: types.Message):
    if m.reply_to_message:
        text = m.reply_to_message.text or m.reply_to_message.caption
    elif len(m.command) > 1:
        text = m.text.split(None, 1)[1]
    else:
        text = None

    if not text or not text.strip():
        return await m.reply_text(m.lang["hw_usage"].format(m.command[0]))

    text = text.strip()[:MAX_CHARS]
    sent = await m.reply_text(m.lang["hw_writing"])

    data = None
    logger.info("API → handwrite: %d chars", len(text))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API}?text={quote(text)}",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    logger.info("API ← handwrite: ok (%d bytes)", len(data))
                else:
                    logger.warning("Handwrite API HTTP %s", resp.status)
    except Exception as ex:
        logger.warning("Handwrite request failed: %s", ex)

    if not data or not data.startswith(_IMG_MAGIC):
        return await sent.edit_text(m.lang["hw_failed"])

    buf = BytesIO(data)
    buf.name = "write.png"
    try:
        await m.reply_photo(
            buf, caption=m.lang["hw_caption"].format(m.from_user.mention)
        )
        await sent.delete()
    except Exception as ex:
        logger.warning("Handwrite send failed: %s", ex)
        await sent.edit_text(m.lang["hw_failed"])
    finally:
        buf.close()

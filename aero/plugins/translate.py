# This file is part of AeroXMusic
# Licensed under the MIT License. See the LICENSE file for details.


from html import escape

import aiohttp
from pyrogram import filters, types

from aero import app, lang, logger

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
MAX_CHARS = 2000


async def _translate(text: str, source: str, dest: str):
    params = {"client": "gtx", "sl": source, "tl": dest, "dt": "t", "q": text}
    logger.info("API → translate: %s→%s (%d chars)", source, dest, len(text))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ENDPOINT, params=params, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    logger.warning("Translate API HTTP %s", resp.status)
                    return None
                data = await resp.json()
                logger.info("API ← translate: ok")
    except Exception as ex:
        logger.warning("Translate request failed: %s", ex)
        return None

    try:
        translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
        detected = data[2] if len(data) > 2 and data[2] else source
    except (IndexError, TypeError):
        return None
    return translated, detected


@app.on_message(filters.command(["tr", "trt"]) & ~app.bl_users)
@lang.language()
async def translate(_, m: types.Message):
    reply = m.reply_to_message
    if not reply or not (reply.text or reply.caption):
        return await m.reply_text(m.lang["tr_reply"])

    source, dest = "auto", "en"
    if len(m.command) > 1:
        arg = m.command[1].lower()
        if "//" in arg:
            source, dest = (arg.split("//", 1) + ["en"])[:2]
            source = source or "auto"
        else:
            dest = arg

    text = (reply.text or reply.caption)[:MAX_CHARS]
    result = await _translate(text, source, dest)
    if not result or not result[0]:
        return await m.reply_text(m.lang["tr_failed"])

    translated, detected = result
    await m.reply_text(
        m.lang["tr_result"].format(detected, dest, escape(translated))
    )

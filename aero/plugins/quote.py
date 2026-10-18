# This file is part of AeroXMusic
# Licensed under the MIT License. See the LICENSE file for details.


from io import BytesIO

import aiohttp
from pyrogram import filters, types

from aero import app, config, lang, logger

# Quote-render backend (the LyoSU/quote-api service). Override via env if the
# default host is down or you self-host your own instance.
QUOTE_API = config.QUOTE_API
BG_COLOR = "#1b1429"
MAX_MESSAGES = 10


def _sender_id(msg: types.Message) -> int:
    if msg.forward_from:
        return msg.forward_from.id
    if msg.forward_from_chat:
        return msg.forward_from_chat.id
    if msg.from_user:
        return msg.from_user.id
    if msg.sender_chat:
        return msg.sender_chat.id
    return 1


def _sender_name(msg: types.Message) -> str:
    if msg.forward_sender_name:
        return msg.forward_sender_name
    user = msg.forward_from or msg.from_user
    if user:
        return f"{user.first_name or ''} {user.last_name or ''}".strip()
    chat = msg.forward_from_chat or msg.sender_chat
    if chat:
        return chat.title or ""
    return ""


def _sender_username(msg: types.Message) -> str:
    user = msg.forward_from or msg.from_user
    if user and user.username:
        return user.username
    chat = msg.forward_from_chat or msg.sender_chat
    if chat and chat.username:
        return chat.username
    return ""


def _sender_photo(msg: types.Message) -> dict:
    user = msg.forward_from or msg.from_user
    chat = msg.forward_from_chat or msg.sender_chat
    photo = (user.photo if user else None) or (chat.photo if chat else None)
    if not photo:
        return {}
    return {
        "small_file_id": photo.small_file_id,
        "small_photo_unique_id": photo.small_photo_unique_id,
        "big_file_id": photo.big_file_id,
        "big_photo_unique_id": photo.big_photo_unique_id,
    }


def _text(msg: types.Message) -> str:
    return msg.text or msg.caption or ""


def _entities(msg: types.Message) -> list:
    ents = msg.entities or msg.caption_entities or []
    return [
        {"type": e.type.name.lower(), "offset": e.offset, "length": e.length}
        for e in ents
    ]


def _build_message(msg: types.Message, is_reply: bool) -> dict:
    data = {
        "entities": _entities(msg),
        "chatId": _sender_id(msg),
        "text": _text(msg),
        "avatar": True,
        "from": {
            "id": _sender_id(msg),
            "name": _sender_name(msg),
            "username": _sender_username(msg),
            "type": msg.chat.type.name.lower(),
            "photo": _sender_photo(msg),
        },
        "replyMessage": {},
    }
    if is_reply and msg.reply_to_message:
        r = msg.reply_to_message
        data["replyMessage"] = {
            "name": _sender_name(r),
            "text": _text(r),
            "chatId": _sender_id(r),
        }
    return data


async def _generate(messages: list, is_reply: bool) -> bytes | None:
    payload = {
        "type": "quote",
        "format": "png",
        "backgroundColor": BG_COLOR,
        "messages": [_build_message(m, is_reply) for m in messages],
    }
    logger.info("API → quote: %d message(s)", len(payload["messages"]))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                QUOTE_API,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Quote API returned HTTP %s", resp.status)
                    return None
                data = await resp.read()
                logger.info("API ← quote: ok (%d bytes)", len(data))
                return data
    except Exception as ex:
        logger.warning("Quote API request failed: %s", ex)
        return None


@app.on_message(filters.command(["q", "qr"]) & filters.group & ~app.bl_users)
@lang.language()
async def quote_cmd(_, m: types.Message):
    if not m.reply_to_message:
        return await m.reply_text(m.lang["quote_reply"])

    is_reply = m.command[0] == "qr"
    count = 1
    if len(m.command) > 1 and m.command[1].isdigit():
        count = max(1, min(int(m.command[1]), MAX_MESSAGES))

    start = m.reply_to_message.id
    try:
        fetched = await app.get_messages(
            m.chat.id, list(range(start, start + count))
        )
    except Exception as ex:
        logger.warning("Quote message fetch failed: %s", ex)
        return await m.reply_text(m.lang["quote_failed"])

    messages = [
        msg for msg in fetched if not msg.empty and (msg.text or msg.caption)
    ]
    if not messages:
        return await m.reply_text(m.lang["quote_failed"])

    image = await _generate(messages, is_reply)
    if not image:
        return await m.reply_text(m.lang["quote_failed"])

    buf = BytesIO(image)
    buf.name = "quote.webp"
    try:
        await m.reply_sticker(buf)
    except Exception as ex:
        logger.warning("Quote sticker send failed: %s", ex)
        await m.reply_text(m.lang["quote_failed"])
    finally:
        buf.close()

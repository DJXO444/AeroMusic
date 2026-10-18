# This file is part of AeroXMusic
# Licensed under the MIT License. See the LICENSE file for details.


import time
from html import escape

from pyrogram import enums, filters, types

from aero import app, db, lang
from aero.helpers import utils

MAX_TARGETS = 4


def _afk_text(_lang: dict, name: str, details: dict, back: bool = False) -> str:
    seen = utils.get_readable_time(int(time.time() - details["time"]))
    name = escape(name[:25])
    reason = details.get("reason")
    if reason:
        key = "afk_back_reason" if back else "afk_since_reason"
        return _lang[key].format(name, seen, escape(reason))
    key = "afk_back" if back else "afk_since"
    return _lang[key].format(name, seen)


async def _announce(
    m: types.Message, _lang: dict, name: str, details: dict, back: bool = False
) -> None:
    text = _afk_text(_lang, name, details, back)
    try:
        if details.get("data"):
            if details["type"] == "animation":
                return await m.reply_animation(details["data"], caption=text)
            if details["type"] == "photo":
                return await m.reply_photo(details["data"], caption=text)
        await m.reply_text(text)
    except Exception:
        pass


@app.on_message(filters.command(["afk", "brb"]) & filters.group & ~app.bl_users)
@lang.language()
async def afk_cmd(_, m: types.Message):
    user_id = m.from_user.id
    was_afk, details = await db.is_afk(user_id)
    if was_afk:
        await db.remove_afk(user_id)
        return await _announce(m, m.lang, m.from_user.first_name, details, back=True)

    reason = (
        m.text.split(None, 1)[1].strip()[:100] if len(m.command) > 1 else None
    )
    media_type, data = "text", None
    replied = m.reply_to_message
    if replied:
        if replied.animation:
            media_type, data = "animation", replied.animation.file_id
        elif replied.photo:
            media_type, data = "photo", replied.photo.file_id

    await db.add_afk(
        user_id,
        {"type": media_type, "time": time.time(), "data": data, "reason": reason},
    )
    await m.reply_text(m.lang["afk_now"].format(escape(m.from_user.first_name[:25])))


@app.on_message(
    filters.group & ~filters.bot & ~filters.via_bot & ~app.bl_users, group=5
)
@lang.language()
async def afk_watcher(_, m: types.Message):
    text = m.text or m.caption or ""
    if text.startswith("/"):
        cmd = text.split(None, 1)[0].split("@", 1)[0].lower()
        if cmd in ("/afk", "/brb"):
            return

    was_afk, details = await db.is_afk(m.from_user.id)
    if was_afk:
        await db.remove_afk(m.from_user.id)
        await _announce(m, m.lang, m.from_user.first_name, details, back=True)

    checked = {m.from_user.id}
    targets = []

    replied = m.reply_to_message
    if replied and replied.from_user and replied.from_user.id not in checked:
        checked.add(replied.from_user.id)
        targets.append(replied.from_user)

    for entity in m.entities or []:
        if len(targets) >= MAX_TARGETS:
            break
        if entity.type == enums.MessageEntityType.TEXT_MENTION and entity.user:
            user = entity.user
        elif entity.type == enums.MessageEntityType.MENTION:
            username = text[entity.offset : entity.offset + entity.length]
            try:
                user = await app.get_users(username)
            except Exception:
                continue
        else:
            continue
        if user.id not in checked:
            checked.add(user.id)
            targets.append(user)

    for user in targets:
        is_away, details = await db.is_afk(user.id)
        if is_away:
            await _announce(m, m.lang, user.first_name, details)

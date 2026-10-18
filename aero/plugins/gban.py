# This file is part of AeroXMusic
# Licensed under the MIT License. See the LICENSE file for details.


import asyncio

from pyrogram import enums, filters, types

from aero import app, db, lang, logger
from aero.helpers import utils

# Self-contained strings (kept here so gban never depends on locale keys).
TXT = {
    "protected": "<blockquote>ᴛʜɪs ᴜsᴇʀ ɪs ᴘʀᴏᴛᴇᴄᴛᴇᴅ ᴀɴᴅ ᴄᴀɴɴᴏᴛ ʙᴇ ɢʙᴀɴɴᴇᴅ.</blockquote>",
    "already": "<blockquote>{0} ɪs ᴀʟʀᴇᴀᴅʏ ɢʟᴏʙᴀʟʟʏ ʙᴀɴɴᴇᴅ.</blockquote>",
    "not": "<blockquote>{0} ɪs ɴᴏᴛ ɢʟᴏʙᴀʟʟʏ ʙᴀɴɴᴇᴅ.</blockquote>",
    "gban_proc": "<blockquote>ɢʟᴏʙᴀʟʟʏ ʙᴀɴɴɪɴɢ {0}...</blockquote>",
    "ungban_proc": "<blockquote>ʀᴇᴍᴏᴠɪɴɢ ɢʟᴏʙᴀʟ ʙᴀɴ ꜰᴏʀ {0}...</blockquote>",
    "gban_done": (
        "<blockquote><b>🌐 ɢʟᴏʙᴀʟʟʏ ʙᴀɴɴᴇᴅ</b>\n\n"
        "<b>ᴜsᴇʀ:</b> {0}\n<b>ɪᴅ:</b> <code>{1}</code>\n"
        "<b>ʙᴀɴɴᴇᴅ ɪɴ:</b> {2} ᴄʜᴀᴛs</blockquote>"
    ),
    "ungban_done": (
        "<blockquote><b>🌐 ɢʟᴏʙᴀʟ ʙᴀɴ ʀᴇᴍᴏᴠᴇᴅ</b>\n\n"
        "<b>ᴜsᴇʀ:</b> {0}\n<b>ᴜɴʙᴀɴɴᴇᴅ ɪɴ:</b> {1} ᴄʜᴀᴛs</blockquote>"
    ),
    "enforced": "<blockquote>{0} ɪs ɢʟᴏʙᴀʟʟʏ ʙᴀɴɴᴇᴅ — ʀᴇᴍᴏᴠᴇᴅ.</blockquote>",
    "no_user": "<blockquote>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ɢɪᴠᴇ ᴀ ᴜsᴇʀɴᴀᴍᴇ / ɪᴅ.</blockquote>",
}


async def _ban_everywhere(user_id: int, unban: bool = False) -> int:
    """(Un)ban a user across every served chat where the bot is admin.
    Returns the number of chats the action succeeded in."""
    done = 0
    for chat_id in list(await db.get_chats()):
        try:
            if unban:
                await app.unban_chat_member(chat_id, user_id)
            else:
                await app.ban_chat_member(chat_id, user_id)
            done += 1
        except Exception:
            # Not admin there / can't ban an admin / chat gone — skip.
            continue
        await asyncio.sleep(0.2)  # gentle on flood limits
    return done


@app.on_message(filters.command(["gban", "globalban"]) & app.sudoers)
@lang.language()
async def _gban(_, m: types.Message):
    user = await utils.extract_user(m)
    if not user:
        return await m.reply_text(TXT["no_user"])

    if user.id == app.owner or user.id in app.sudoers:
        return await m.reply_text(TXT["protected"])
    if await db.is_gbanned(user.id):
        return await m.reply_text(TXT["already"].format(user.mention))

    sent = await m.reply_text(TXT["gban_proc"].format(user.mention))
    await db.add_gban(user.id)
    count = await _ban_everywhere(user.id)
    logger.info("gban: %s banned across %d chats", user.id, count)
    await sent.edit_text(TXT["gban_done"].format(user.mention, user.id, count))


@app.on_message(filters.command(["ungban", "globalunban"]) & app.sudoers)
@lang.language()
async def _ungban(_, m: types.Message):
    user = await utils.extract_user(m)
    if not user:
        return await m.reply_text(TXT["no_user"])

    if not await db.is_gbanned(user.id):
        return await m.reply_text(TXT["not"].format(user.mention))

    sent = await m.reply_text(TXT["ungban_proc"].format(user.mention))
    await db.del_gban(user.id)
    count = await _ban_everywhere(user.id, unban=True)
    logger.info("ungban: %s unbanned across %d chats", user.id, count)
    await sent.edit_text(TXT["ungban_done"].format(user.mention, count))


# Enforcement: auto-ban a gbanned user the moment they join any group.
@app.on_message(filters.new_chat_members & filters.group, group=9)
async def _gban_enforce(_, m: types.Message):
    for user in m.new_chat_members or []:
        if await db.is_gbanned(user.id):
            try:
                await app.ban_chat_member(m.chat.id, user.id)
                await m.reply_text(TXT["enforced"].format(user.mention))
            except Exception:
                pass

# Licensed under the MIT License.
# This file is part of AeroXMusic


import asyncio
import re

from pyrogram import filters, types

from aero import app, db, lang
from aero.helpers import admin_check

# Emojis match anywhere in the message; words match only as standalone words
# (so "good" triggers but "goodbye" doesn't).
upvote_pat = re.compile(
    r"(👍|🖤|❣️|💝|💖|💕|❤|💘|\+\+|\+1|\+69"
    r"|\b(?:thx|thanx|thanks|thankyou|cool|good|baby|love|pro)\b)",
    re.IGNORECASE,
)
downvote_pat = re.compile(
    r"(👎|💔|--|\-1"
    r"|\bfuck off\b|\b(?:noob|weak|nub|gey|kid|shit|mf)\b)",
    re.IGNORECASE,
)


@app.on_message(
    filters.text
    & filters.group
    & filters.reply
    & ~filters.via_bot
    & ~filters.bot
    & ~app.bl_users
    & filters.regex(upvote_pat),
    group=3,
)
@lang.language()
async def upvote(_, m: types.Message):
    if not await db.is_karma_on(m.chat.id):
        return
    target = m.reply_to_message.from_user
    if not target or not m.from_user:
        return
    if target.id == app.owner:
        return await m.reply_text(m.lang["karma_owner_protect"])
    if target.id == m.from_user.id:
        return await m.reply_text(m.lang["karma_self"])

    doc = await db.get_karma(m.chat.id, target.id)
    karma = (doc["karma"] + 1) if doc else 1
    await db.update_karma(m.chat.id, target.id, {"karma": karma})
    await m.reply_text(
        m.lang["karma_incremented"].format(target.mention, karma)
    )


@app.on_message(
    filters.text
    & filters.group
    & filters.reply
    & ~filters.via_bot
    & ~filters.bot
    & ~app.bl_users
    & filters.regex(downvote_pat),
    group=4,
)
@lang.language()
async def downvote(_, m: types.Message):
    if not await db.is_karma_on(m.chat.id):
        return
    target = m.reply_to_message.from_user
    if not target or not m.from_user:
        return
    if target.id == app.owner:
        return await m.reply_text(m.lang["karma_owner_protect"])
    if target.id == m.from_user.id:
        return await m.reply_text(m.lang["karma_self"])

    doc = await db.get_karma(m.chat.id, target.id)
    karma = (doc["karma"] - 1) if doc else 0
    await db.update_karma(m.chat.id, target.id, {"karma": karma})
    await m.reply_text(
        m.lang["karma_decremented"].format(target.mention, karma)
    )


@app.on_message(filters.command(["karmastat"]) & filters.group & ~app.bl_users)
@lang.language()
@admin_check
async def karma_stat(_, m: types.Message):
    if m.reply_to_message:
        target = m.reply_to_message.from_user
        doc = await db.get_karma(m.chat.id, target.id)
        karma = doc["karma"] if doc else 0
        return await m.reply_text(
            m.lang["karma_total"].format(karma)
        )

    sent = await m.reply_text(m.lang["karma_fetching"])
    raw = await db.get_karmas(m.chat.id)
    if not raw:
        return await sent.edit_text(m.lang["karma_empty"])

    arranged = dict(
        sorted(raw.items(), key=lambda x: x[1]["karma"], reverse=True)
    )
    msg = m.lang["karma_title"].format(m.chat.title) + "\n"
    limit = 0
    for uid_str, data in arranged.items():
        if limit >= 10:
            break
        try:
            user = await app.get_users(int(uid_str))
            await asyncio.sleep(0.8)
        except Exception:
            continue
        if not user.first_name:
            continue
        name = user.first_name
        link = f"https://t.me/{user.username}" if user.username else f"tg://user?id={uid_str}"
        msg += f"\n≛ [{name}]({link}) : {data['karma']}"
        limit += 1

    await sent.edit_text(msg, disable_web_page_preview=True)


@app.on_message(filters.command(["karma"]) & filters.group & ~app.bl_users)
@lang.language()
@admin_check
async def karma_toggle(_, m: types.Message):
    if len(m.command) != 2:
        return await m.reply_text(m.lang["karma_usage"].format(m.command[0]))

    state = m.command[1].strip().lower()
    if state == "on":
        await db.karma_on(m.chat.id)
        await m.reply_text(m.lang["karma_on"])
    elif state == "off":
        await db.karma_off(m.chat.id)
        await m.reply_text(m.lang["karma_off"])
    else:
        await m.reply_text(m.lang["karma_usage"].format(m.command[0]))

# Licensed under the MIT License.
# This file is part of AeroXMusic

import asyncio
import inspect
import random

from pyrogram import enums, filters, types

from aero import app, config, db, lang
from aero.helpers import buttons, utils

# Full-screen message effect for the start message (private chats only).
FIRE_EFFECT = 5104841245755180586
_HAS_EFFECT = "effect_id" in inspect.signature(types.Message.reply_photo).parameters

START_EMOJIS = [
    "<emoji id=6238004754044163412>🎶</emoji>",
    "<emoji id=6237545634925123920>🎶</emoji>",
]


@app.on_message(filters.command(["help"]) & filters.private & ~app.bl_users)
@lang.language()
async def _help(_, m: types.Message):
    await m.reply_text(
        text=m.lang["help_menu"],
        reply_markup=buttons.commands_menu(m.lang),
        quote=True,
    )


@app.on_message(filters.command(["help"]) & filters.group & ~app.bl_users)
@lang.language()
async def _help_group(_, m: types.Message):
    await m.reply_text(
        text=m.lang["help_pm"],
        reply_markup=buttons.help_pm(m.lang["help_pm_btn"]),
        quote=True,
    )


@app.on_message(filters.command(["start"]))
@lang.language()
async def start(_, message: types.Message):
    if message.from_user.id in app.bl_users and message.from_user.id not in db.notified:
        return await message.reply_text(message.lang["bl_user_notify"])

    if len(message.command) > 1 and message.command[1] == "help":
        return await _help(_, message)

    private = message.chat.type == enums.ChatType.PRIVATE

    try:
        await message.react("❤️", big=True)
    except Exception:
        pass

    try:
        intro = await message.reply_text(random.choice(START_EMOJIS))
        await asyncio.sleep(2.5)
        await intro.delete()
    except Exception:
        pass

    _text = (
        message.lang["start_pm"].format(message.from_user.first_name, app.name)
        if private
        else message.lang["start_gp"].format(app.name)
    )

    key = buttons.start_key(message.lang, private)
    photo_args = dict(
        photo=config.START_IMG,
        caption=_text,
        reply_markup=key,
        quote=not private,
    )
    if private and _HAS_EFFECT:
        photo_args["effect_id"] = FIRE_EFFECT
    try:
        await message.reply_photo(**photo_args)
    except Exception:
        # Effect may be rejected (invalid id / non-premium) — resend plainly.
        photo_args.pop("effect_id", None)
        try:
            await message.reply_photo(**photo_args)
        except Exception:
            pass

    if private:
        if await db.is_user(message.from_user.id):
            return
        await utils.send_log(message)
        await db.add_user(message.from_user.id)
    else:
        if await db.is_chat(message.chat.id):
            return
        await utils.send_log(message, True)
        await db.add_chat(message.chat.id)


@app.on_message(filters.command(["playmode", "settings"]) & filters.group & ~app.bl_users)
@lang.language()
async def settings(_, message: types.Message):
    admin_only = await db.get_play_mode(message.chat.id)
    cmd_delete = await db.get_cmd_delete(message.chat.id)
    _language = await db.get_lang(message.chat.id)
    await message.reply_text(
        text=message.lang["start_settings"].format(message.chat.title),
        reply_markup=buttons.settings_markup(
            message.lang, admin_only, cmd_delete, _language, message.chat.id
        ),
        quote=True,
    )


@app.on_message(filters.new_chat_members, group=7)
@lang.language()
async def _new_member(_, message: types.Message):
    if message.chat.type != enums.ChatType.SUPERGROUP:
        return await message.chat.leave()

    if not any(u.id == app.id for u in message.new_chat_members):
        return

    # The bot itself was added -> greet the group with the start card.
    try:
        await message.reply_photo(
            photo=config.START_IMG,
            caption=message.lang["start_gp"].format(app.name),
            reply_markup=buttons.start_key(message.lang, False),
        )
    except Exception:
        pass

    if not await db.is_chat(message.chat.id):
        await utils.send_log(message, True)
        await db.add_chat(message.chat.id)

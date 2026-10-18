# This file is part of AeroXMusic
# Licensed under the MIT License. See the LICENSE file for details.


from io import BytesIO

from pyrogram import errors, filters, types

from aero import app, lang, logger
from aero.helpers import admin_check


@app.on_message(filters.command(["givelink"]) & filters.group & ~app.bl_users)
@lang.language()
@admin_check
async def give_link(_, m: types.Message):
    try:
        link = await app.export_chat_invite_link(m.chat.id)
    except errors.ChatAdminRequired:
        return await m.reply_text(m.lang["gl_no_perms"])
    except Exception as ex:
        logger.warning("givelink failed: %s", ex)
        return await m.reply_text(m.lang["gl_error"])
    await m.reply_text(m.lang["gl_link"].format(link))


@app.on_message(filters.command(["getlink", "invitelink"]) & app.sudoers)
@lang.language()
async def get_link(_, m: types.Message):
    if len(m.command) != 2:
        return await m.reply_text(m.lang["gl_usage"].format(m.command[0]))

    try:
        chat_id = int(m.command[1])
    except ValueError:
        return await m.reply_text(m.lang["gl_usage"].format(m.command[0]))

    try:
        chat = await app.get_chat(chat_id)
    except Exception as ex:
        logger.warning("getlink get_chat failed for %s: %s", chat_id, ex)
        return await m.reply_text(m.lang["gl_error"])

    try:
        invite = await app.export_chat_invite_link(chat.id)
    except errors.FloodWait as ex:
        return await m.reply_text(m.lang["gl_flood"].format(ex.value))
    except Exception:
        invite = None

    fields = {
        "id": chat.id,
        "type": chat.type.name,
        "title": chat.title,
        "members_count": getattr(chat, "members_count", None),
        "description": getattr(chat, "description", None),
        "invite_link": invite,
        "dc_id": getattr(chat, "dc_id", None),
        "is_verified": getattr(chat, "is_verified", None),
        "is_restricted": getattr(chat, "is_restricted", None),
        "is_scam": getattr(chat, "is_scam", None),
        "is_fake": getattr(chat, "is_fake", None),
        "has_protected_content": getattr(chat, "has_protected_content", None),
    }
    body = "\n".join(f"{key}: {value}" for key, value in fields.items())

    buf = BytesIO(body.encode("utf-8"))
    buf.name = f"group_{chat.id}.txt"
    try:
        await m.reply_document(
            buf, caption=m.lang["gl_caption"].format(chat.title, app.username)
        )
    except Exception as ex:
        logger.warning("getlink send failed: %s", ex)
        await m.reply_text(m.lang["gl_error"])
    finally:
        buf.close()

# This file is part of AeroXMusic
# Licensed under the MIT License. See the LICENSE file for details.


import asyncio
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw
from pyrogram import enums, filters, types

from aero import app, db, lang, logger
from aero.helpers import admin_check, buttons

TEMPLATE_PATH = "aero/helpers/welcome.jpg"
FALLBACK_PFP = "aero/helpers/aero.jpg"

# Short-lived dedup so a member added-by-someone (which fires BOTH the service
# message and the chat_member update) is welcomed only once.
_welcomed: set = set()

_IN_STATUSES = (
    enums.ChatMemberStatus.MEMBER,
    enums.ChatMemberStatus.RESTRICTED,
)
_PRESENT = _IN_STATUSES + (
    enums.ChatMemberStatus.ADMINISTRATOR,
    enums.ChatMemberStatus.OWNER,
)

# Circle position in the template.
PFP_SIZE = 362
CIRCLE_CX = 800
CIRCLE_CY = 248
RING_WIDTH = 6


def _circle_mask(size: int) -> Image.Image:
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big, big), fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def _square_crop(img: Image.Image) -> Image.Image:
    w, h = img.size
    s = min(w, h)
    return img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))


def _build_image(pfp_bytes: bytes | None) -> BytesIO:
    bg = Image.open(TEMPLATE_PATH).convert("RGBA")

    try:
        source = BytesIO(pfp_bytes) if pfp_bytes else FALLBACK_PFP
        pfp = Image.open(source).convert("RGBA")
    except Exception:
        pfp = Image.open(FALLBACK_PFP).convert("RGBA")

    pfp = _square_crop(pfp).resize((PFP_SIZE, PFP_SIZE), Image.LANCZOS)
    mask = _circle_mask(PFP_SIZE)
    alpha = ImageChops.darker(mask, pfp.split()[-1])
    pfp.putalpha(alpha)

    x = CIRCLE_CX - PFP_SIZE // 2
    y = CIRCLE_CY - PFP_SIZE // 2

    ring_d = PFP_SIZE + RING_WIDTH * 2
    ring = Image.new("RGBA", (ring_d, ring_d), (0, 0, 0, 0))
    ring.paste((255, 255, 255, 255), (0, 0), _circle_mask(ring_d))
    bg.paste(ring, (x - RING_WIDTH, y - RING_WIDTH), ring)
    bg.paste(pfp, (x, y), pfp)

    buf = BytesIO()
    buf.name = "welcome.png"
    bg.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


async def _fetch_pfp(user_id: int) -> bytes | None:
    src = None
    try:
        async for photo in app.get_chat_photos(user_id, limit=1):
            src = await app.download_media(photo.file_id, in_memory=True)
            if src:
                return bytes(src.getbuffer())
    except Exception as ex:
        logger.warning("Welcome: pfp fetch failed for %s: %s", user_id, ex)
    finally:
        if src is not None:
            src.close()
    return None


async def _send_welcome(chat_id: int, chat_title: str, user: types.User) -> None:
    pfp_bytes = await _fetch_pfp(user.id)
    image = None
    try:
        image = await asyncio.to_thread(_build_image, pfp_bytes)
        _lang = await lang.get_lang(chat_id)
        username = f"@{user.username}" if user.username else "—"
        caption = _lang["welcome_caption"].format(
            chat_title, user.mention, user.id, username
        )
        await app.send_photo(
            chat_id,
            photo=image,
            caption=caption,
            reply_markup=buttons.welcome_key(_lang["welcome_add"]),
        )
    except Exception as ex:
        logger.warning("Welcome: send failed in %s: %s", chat_id, ex)
    finally:
        if image is not None:
            image.close()


async def _forget(key) -> None:
    await asyncio.sleep(45)
    _welcomed.discard(key)


async def _maybe_welcome(chat_id: int, title: str, user: types.User) -> None:
    if not user or user.is_bot:
        return
    if chat_id in db.blacklisted or not await db.get_welcome(chat_id):
        return
    key = (chat_id, user.id)
    if key in _welcomed:
        return  # already welcomed via the other event
    _welcomed.add(key)
    asyncio.create_task(_forget(key))
    await _send_welcome(chat_id, title, user)


# 1) Service message ("X added Y") — fires when a member is added by someone.
@app.on_message(filters.new_chat_members & filters.group, group=6)
async def _welcome_new(_, m: types.Message):
    title = m.chat.title or "the group"
    for user in m.new_chat_members or []:
        await _maybe_welcome(m.chat.id, title, user)


# 2) Membership update — fires for ALL joins (self-join, invite link, etc.) as
# long as the bot is an admin. This is what supergroups actually use.
@app.on_chat_member_updated(group=6)
async def _welcome_join(_, upd: types.ChatMemberUpdated):
    new = upd.new_chat_member
    old = upd.old_chat_member
    if not new or not new.user:
        return
    was_present = old is not None and old.status in _PRESENT
    is_present = new.status in _IN_STATUSES
    if not was_present and is_present:
        await _maybe_welcome(
            upd.chat.id, upd.chat.title or "the group", new.user
        )


@app.on_message(filters.command(["welcome"]) & filters.group & ~app.bl_users)
@lang.language()
@admin_check
async def _welcome_toggle(_, m: types.Message):
    if len(m.command) != 2 or m.command[1].lower() not in ("on", "off"):
        return await m.reply_text(m.lang["welcome_usage"].format(m.command[0]))
    enable = m.command[1].lower() == "on"
    await db.set_welcome(m.chat.id, enable)
    await m.reply_text(
        m.lang["welcome_on"] if enable else m.lang["welcome_off"]
    )

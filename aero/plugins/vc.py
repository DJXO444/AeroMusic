# This file is part of AeroXMusic
# Licensed under the MIT License. See the LICENSE file for details.


import asyncio
from html import escape

from pyrogram import enums, errors, filters, raw, types

from aero import app, db, lang, logger, tasks, userbot
from aero.helpers import admin_check, buttons, utils

MAX_INVITED = 10
LOG_DELETE = 10
POLL_SECONDS = 2

_link_cache: dict[int, str] = {}
_known: dict[int, set[int]] = {}
_active_vcs: set[int] = set()
_recent_joins: set = set()  # (chat_id, user_id) — dedup poller vs realtime updates


async def _forget_join(key) -> None:
    await asyncio.sleep(LOG_DELETE + 15)
    _recent_joins.discard(key)


async def _resolve_link(chat_id: int) -> str:
    """Best link to the group's voice chat: public -> ?videochat deep link,
    private -> invite link, or "" when we can't get one."""
    try:
        chat = await app.get_chat(chat_id)
    except Exception:
        return ""
    if chat.username:
        return f"https://t.me/{chat.username}?videochat"
    try:
        link = chat.invite_link or await app.export_chat_invite_link(chat_id)
        return link or ""
    except Exception:
        return ""


async def _vc_join_button(chat_id: int, _lang: dict):
    if chat_id not in _link_cache:
        _link_cache[chat_id] = await _resolve_link(chat_id)
    url = _link_cache[chat_id]
    if url:
        return buttons.vc_join(_lang["vc_join_btn"], url)
    # No usable link (e.g. private group, bot lacks invite permission):
    # still show the button, but it has no target.
    return buttons.vc_join_blank(_lang["vc_join_btn"])


async def _resolve_user(user_id: int, client=None):
    """Resolve a user object for the VC alert.

    The BOT often hasn't "met" a VC participant, so app.get_users raises
    PEER_ID_INVALID. The assistant that polled the call HAS met them (it just
    read them from the participant list), so try it first, then fall back to
    the bot. Returns a user object or None."""
    for resolver in (client, app):
        if resolver is None:
            continue
        try:
            return await resolver.get_users(user_id)
        except Exception as ex:
            logger.warning(
                "vclog: %s.get_users(%s) failed: %s",
                "assistant" if resolver is client else "bot", user_id, ex,
            )
    return None


async def notify_vc_join(chat_id: int, user_id: int, client=None) -> None:
    if not await db.get_vclog(chat_id):
        return
    if user_id == app.id or any(
        getattr(c, "id", None) == user_id for c in userbot.clients
    ):
        return
    key = (chat_id, user_id)
    if key in _recent_joins:
        return  # already alerted (realtime update + poller can both fire)
    _recent_joins.add(key)
    asyncio.create_task(_forget_join(key))

    # Resolve via the assistant first (it knows the peer), then the bot. NEVER
    # let a lookup failure swallow the alert — fall back to a plain mention.
    user = await _resolve_user(user_id, client)
    if user is not None and getattr(user, "is_bot", False):
        return
    if user is not None:
        mention = user.mention
    else:
        mention = f'<a href="tg://user?id={user_id}">User</a>'

    _lang = await lang.get_lang(chat_id)
    try:
        admins = await db.get_admins(chat_id)
    except Exception:
        admins = []
    role = _lang["vc_admin"] if user_id in admins else _lang["vc_member"]

    markup = await _vc_join_button(chat_id, _lang)
    try:
        sent = await app.send_message(
            chat_id,
            _lang["vc_joined"].format(mention, user_id, role),
            reply_markup=markup,
        )
    except Exception as ex:
        logger.warning("vclog: alert send failed in %s: %s", chat_id, ex)
        _recent_joins.discard(key)  # let a later attempt retry
        return

    await asyncio.sleep(LOG_DELETE)
    try:
        await sent.delete()
    except Exception:
        pass


async def _ensure_assistant(chat_id: int) -> bool:
    """Make sure an assistant userbot is a member of the group so the poller can
    read its voice-chat participants (no need to join the call). Returns True if
    the assistant is/becomes a member."""
    client = await db.get_client(chat_id)
    if not client:
        return False
    try:
        member = await app.get_chat_member(chat_id, client.id)
        if member.status not in (
            enums.ChatMemberStatus.BANNED,
            enums.ChatMemberStatus.RESTRICTED,
        ):
            return True  # already a member
    except errors.UserNotParticipant:
        pass
    except Exception as ex:
        logger.warning("vclog: membership check failed in %s: %s", chat_id, ex)
        return False
    # Not a member yet — join via public username, else an invite link (needs
    # the bot to have invite-users permission).
    try:
        chat = await app.get_chat(chat_id)
        if chat.username:
            await client.join_chat(chat.username)
        else:
            link = chat.invite_link or await app.export_chat_invite_link(chat_id)
            await client.join_chat(link)
        logger.info("vclog: assistant joined %s", chat_id)
        return True
    except errors.UserAlreadyParticipant:
        return True
    except Exception as ex:
        logger.warning("vclog: assistant couldn't join %s: %s", chat_id, ex)
        return False


@app.on_message(filters.new_chat_members & filters.group, group=8)
async def _bot_added(_, m: types.Message):
    """When the bot is added to a group, pull an assistant into it so vclog
    works automatically (as long as vclog isn't turned off)."""
    if m.chat.id in db.blacklisted:
        return
    if any(u.id == app.id for u in (m.new_chat_members or [])):
        if await db.get_vclog(m.chat.id):
            await _ensure_assistant(m.chat.id)


@app.on_message(filters.command(["vclog"]) & filters.group & ~app.bl_users)
@lang.language()
@admin_check
async def vclog_cmd(_, m: types.Message):
    if len(m.command) < 2 or m.command[1].lower() not in ("on", "off"):
        return await m.reply_text(m.lang["vclog_usage"].format(m.command[0]))
    enable = m.command[1].lower() == "on"
    await db.set_vclog(m.chat.id, enable)
    if enable:
        await _ensure_assistant(m.chat.id)
        # Arm the poller now if a VC is already open (we may have missed the
        # video_chat_started service message, e.g. after a restart).
        _active_vcs.add(m.chat.id)
        _known.pop(m.chat.id, None)
    else:
        _drop(m.chat.id)
    await m.reply_text(m.lang["vclog_on"] if enable else m.lang["vclog_off"])


# --------------------------------------------------------------- VC join poller
# Detects voice-chat joins by polling participants over raw MTProto with an
# assistant that is a group MEMBER — the assistant does NOT need to be in the
# call itself.
async def _active_call(client, chat_id: int):
    peer = await client.resolve_peer(chat_id)
    if isinstance(peer, raw.types.InputPeerChannel):
        full = await client.invoke(
            raw.functions.channels.GetFullChannel(
                channel=raw.types.InputChannel(
                    channel_id=peer.channel_id, access_hash=peer.access_hash
                )
            )
        )
    elif isinstance(peer, raw.types.InputPeerChat):
        full = await client.invoke(
            raw.functions.messages.GetFullChat(chat_id=peer.chat_id)
        )
    else:
        return None
    return getattr(full.full_chat, "call", None)


async def _participant_ids(client, call) -> set[int]:
    ids: set[int] = set()
    offset = ""
    while True:
        res = await client.invoke(
            raw.functions.phone.GetGroupParticipants(
                call=call, ids=[], sources=[], offset=offset, limit=200
            )
        )
        for p in res.participants:
            if getattr(p, "left", False):
                continue
            if isinstance(p.peer, raw.types.PeerUser):
                ids.add(p.peer.user_id)
        if not res.next_offset:
            break
        offset = res.next_offset
    return ids


def _drop(chat_id: int) -> None:
    _active_vcs.discard(chat_id)
    _known.pop(chat_id, None)


async def _poll_chat(chat_id: int) -> None:
    if not await db.get_vclog(chat_id):
        _drop(chat_id)
        return
    client = await db.get_client(chat_id)
    if not client:
        logger.warning("vclog: no assistant client for %s — cannot poll", chat_id)
        _drop(chat_id)
        return
    call = await _active_call(client, chat_id)
    if call is None:
        logger.info("vclog: no active call in %s — stopping poll", chat_id)
        _drop(chat_id)  # voice chat ended
        return
    # Confirmed an active call — keep this chat armed even after music stops,
    # until the VC actually ends.
    _active_vcs.add(chat_id)
    current = await _participant_ids(client, call)
    prev = _known.get(chat_id)
    _known[chat_id] = current
    if prev is None:
        logger.info("vclog: baseline for %s = %d participant(s)", chat_id, len(current))
        return  # first sighting — baseline without announcing existing members
    joined = current - prev
    if joined:
        logger.info("vclog: %s new join(s) in %s: %s", len(joined), chat_id, list(joined))
    for uid in joined:
        # Pass the polling assistant — it has met these peers, so it can resolve
        # names the bot can't (avoids PEER_ID_INVALID -> "User").
        asyncio.create_task(notify_vc_join(chat_id, uid, client))


async def _vc_watcher() -> None:
    logger.info("vclog: participant poller started (%ss interval)", POLL_SECONDS)
    while True:
        await asyncio.sleep(POLL_SECONDS)
        # Poll chats armed by a VC-start event PLUS any chat currently playing
        # music (an assistant is definitely in those), so detection works even
        # if we missed the service message or restarted mid-call.
        targets = _active_vcs | set(db.active_calls)
        for chat_id in targets:
            if not await db.get_vclog(chat_id):
                _drop(chat_id)
                continue
            try:
                await _poll_chat(chat_id)
            except errors.FloodWait as ex:
                await asyncio.sleep(ex.value)
            except errors.RPCError as ex:
                # GROUPCALL_INVALID / call ended -> stop polling this chat.
                # Anything else is likely transient (or a resolve/permission
                # issue we must SEE) — log it and keep polling next round.
                name = getattr(ex, "ID", "") or type(ex).__name__
                if "GROUPCALL" in name or "CALL_ALREADY" in name:
                    _drop(chat_id)
                else:
                    logger.warning("vclog: poll RPC error in %s: %s", chat_id, name)
            except Exception as ex:
                logger.warning("vclog: poll error in %s: %r", chat_id, ex)


tasks.append(asyncio.create_task(_vc_watcher()))


def _sender(m: types.Message) -> tuple[str, int]:
    if m.from_user:
        return m.from_user.mention, m.from_user.id
    if m.sender_chat:
        return escape(m.sender_chat.title or ""), m.sender_chat.id
    return "Anonymous", 0


@app.on_message(
    filters.video_chat_started & filters.group & ~app.bl_users, group=10
)
async def vc_started(_, m: types.Message):
    if m.chat.id in db.blacklisted:
        return
    if await db.get_vclog(m.chat.id):
        # Ensure an assistant is a group member first, so the poller can read
        # participants over raw MTProto (it does not need to join the call).
        await _ensure_assistant(m.chat.id)
        _active_vcs.add(m.chat.id)
        _known.pop(m.chat.id, None)
        logger.info("vclog: VC started in %s — polling armed", m.chat.id)
    _lang = await lang.get_lang(m.chat.id)
    name, user_id = _sender(m)
    try:
        await m.reply_text(_lang["vc_started"].format(name, user_id))
    except Exception:
        pass


@app.on_message(
    filters.video_chat_ended & filters.group & ~app.bl_users, group=10
)
async def vc_ended(_, m: types.Message):
    if m.chat.id in db.blacklisted:
        return
    _drop(m.chat.id)
    _lang = await lang.get_lang(m.chat.id)
    name, user_id = _sender(m)
    duration = utils.get_readable_time(m.video_chat_ended.duration or 0)
    try:
        await m.reply_text(_lang["vc_ended"].format(name, user_id, duration))
    except Exception:
        pass


@app.on_message(
    filters.video_chat_members_invited & filters.group & ~app.bl_users, group=10
)
async def vc_invited(_, m: types.Message):
    if m.chat.id in db.blacklisted:
        return
    users = (m.video_chat_members_invited.users or [])[:MAX_INVITED]
    if not users:
        return
    _lang = await lang.get_lang(m.chat.id)
    name, _ = _sender(m)
    invited = ", ".join(u.mention for u in users)
    try:
        await m.reply_text(_lang["vc_invited"].format(name, invited))
    except Exception:
        pass

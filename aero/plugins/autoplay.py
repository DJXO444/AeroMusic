# Licensed under the MIT License.
# This file is part of AeroXMusic


import random
import re
import types as _types

import aiohttp
from py_yt import VideosSearch
from pyrogram import filters, types

from aero import aero, app, config, db, lang, logger, queue, yt
from aero.helpers import Track, buttons, can_manage_vc, utils


# ── Track memory ─────────────────────────────────────────────────────────────

_last_track: dict[int, str] = {}
_last_track_id: dict[int, str] = {}
_last_channel: dict[int, str] = {}
_used_ids: dict[int, set] = {}


def remember_track(
    chat_id: int, title: str, track_id: str = "", channel: str = ""
) -> None:
    if title:
        _last_track[chat_id] = title
    if track_id:
        _last_track_id[chat_id] = track_id
        _used_ids.setdefault(chat_id, set()).add(track_id)
    if channel:
        _last_channel[chat_id] = channel.lower()


# ── Primary: YouTube InnerTube "Up Next" ─────────────────────────────────────

_INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/next"
# Public web client key used by youtube.com itself.
_INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"


async def _fetch_upnext(video_id: str, limit: int = 15) -> list[Track]:
    """Fetch YouTube's own 'Up Next' recommendation list for a video.

    Uses the same InnerTube endpoint the youtube.com watch page calls, so the
    results are the real co-watch recommendations — not title-matched search.
    """
    if not video_id:
        return []
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20240722.01.00",
                "hl": "en",
                "gl": "US",
            }
        },
        "videoId": video_id,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_INNERTUBE_URL}?key={_INNERTUBE_KEY}",
                json=payload,
                timeout=aiohttp.ClientTimeout(20),
            ) as resp:
                if resp.status != 200:
                    logger.warning("up-next: HTTP %s", resp.status)
                    return []
                data = await resp.json()

        results = (
            data.get("contents", {})
            .get("twoColumnWatchNextResults", {})
            .get("secondaryResults", {})
            .get("secondaryResults", {})
            .get("results", [])
        )

        tracks = []
        for item in results:
            lv = item.get("lockupViewModel")
            if not lv:
                continue
            if lv.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
                continue
            vid_id = lv.get("contentId")
            if not vid_id:
                continue

            meta = lv.get("metadata", {}).get("lockupMetadataViewModel", {})
            title = (meta.get("title", {}) or {}).get("content") or ""

            channel = ""
            rows = (
                meta.get("metadata", {})
                .get("contentMetadataViewModel", {})
                .get("metadataRows")
                or []
            )
            for row in rows:
                for part in row.get("metadataParts") or []:
                    text = (part.get("text", {}) or {}).get("content")
                    if text:
                        channel = text
                        break
                if channel:
                    break

            duration = "0:00"
            overlays = (
                lv.get("contentImage", {})
                .get("thumbnailViewModel", {})
                .get("overlays")
                or []
            )
            for overlay in overlays:
                badges = (
                    overlay.get("thumbnailBottomOverlayViewModel", {}).get("badges")
                    or []
                )
                for badge in badges:
                    text = badge.get("thumbnailBadgeViewModel", {}).get("text", "")
                    if ":" in text:
                        duration = text
                        break
                if ":" in duration:
                    break

            sources = (
                lv.get("contentImage", {})
                .get("thumbnailViewModel", {})
                .get("image", {})
                .get("sources")
                or []
            )
            thumb_url = sources[-1].get("url", "") if sources else ""

            tracks.append(
                Track(
                    id=vid_id,
                    channel_name=channel[:30],
                    duration=duration,
                    duration_sec=utils.to_seconds(duration),
                    title=title[:25],
                    url=f"https://www.youtube.com/watch?v={vid_id}",
                    thumbnail=thumb_url.split("?")[0],
                    view_count="",
                )
            )
            if len(tracks) >= limit:
                break

        logger.info("up-next ← %d tracks for %s", len(tracks), video_id)
        return tracks
    except Exception as e:
        logger.warning(f"up-next error: {e}")
        return []


def _keyword_list(title: str) -> list[str]:
    stop_words = {
        "the", "a", "an", "is", "in", "on", "and", "or", "of", "to",
        "tu", "hai", "ho", "hoon", "main", "mein", "ke", "ka", "ki",
        "se", "ne",
    }
    words = re.sub(r"[^\w\s]", "", title.lower()).split()
    return [w for w in words if w not in stop_words and len(w) > 2]


def _pick_related(
    chat_id: int, tracks: list[Track], keywords: list[str]
) -> Track | None:
    used_ids = _used_ids.setdefault(chat_id, set())
    last_channel = _last_channel.get(chat_id)
    for track in tracks:
        if not track.id or track.id in used_ids:
            continue
        if track.duration_sec > config.DURATION_LIMIT or track.duration_sec < 60:
            continue
        if last_channel and (track.channel_name or "").lower() == last_channel:
            continue
        t_lower = (track.title or "").lower()
        if sum(1 for kw in keywords if kw in t_lower) >= 2:
            continue
        return track
    return None


# ── Fallback 2: YouTube Data API (title search, music category) ──────────────

def _iso8601_to_seconds(duration: str) -> int:
    """Convert PT#H#M#S (YouTube contentDetails) to total seconds."""
    if not duration:
        return 0
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", duration)
    if not match:
        return 0
    hours, mins, secs = (int(g or 0) for g in match.groups())
    return hours * 3600 + mins * 60 + secs


async def _fetch_related(title: str, limit: int = 15) -> list[Track]:
    """Search music-category tracks via the YouTube Data API."""
    if not config.YT_DATA_API_KEY:
        return []
    base = "https://www.googleapis.com/youtube/v3"
    params = {
        "part": "snippet",
        "type": "video",
        "videoCategoryId": "10",
        "q": title,
        "maxResults": limit,
        "key": config.YT_DATA_API_KEY,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base}/search", params=params, timeout=aiohttp.ClientTimeout(15)
            ) as resp:
                if resp.status != 200:
                    logger.warning("yt-data search: HTTP %s", resp.status)
                    return []
                data = await resp.json()

            items = data.get("items", [])
            if not items:
                return []

            ids = [item["id"]["videoId"] for item in items if item.get("id")]
            duration_params = {
                "part": "contentDetails",
                "id": ",".join(ids),
                "key": config.YT_DATA_API_KEY,
            }
            async with session.get(
                f"{base}/videos", params=duration_params, timeout=aiohttp.ClientTimeout(15)
            ) as resp:
                if resp.status != 200:
                    logger.warning("yt-data videos: HTTP %s", resp.status)
                    return []
                vdata = await resp.json()

            durations = {
                item["id"]: _iso8601_to_seconds(
                    item.get("contentDetails", {}).get("duration", "")
                )
                for item in vdata.get("items", [])
            }

            tracks = []
            for item in items:
                vid_id = item.get("id", {}).get("videoId")
                snippet = item.get("snippet", {})
                if not vid_id:
                    continue
                thumbs = snippet.get("thumbnails", {})
                thumb_url = (
                    thumbs.get("high", {}).get("url")
                    or thumbs.get("medium", {}).get("url")
                    or thumbs.get("default", {}).get("url")
                    or ""
                )
                tracks.append(
                    Track(
                        id=vid_id,
                        channel_name=snippet.get("channelTitle", ""),
                        duration_sec=durations.get(vid_id, 0),
                        title=(snippet.get("title") or "")[:25],
                        url=f"https://www.youtube.com/watch?v={vid_id}",
                        thumbnail=thumb_url.split("?")[0],
                        view_count="",
                    )
                )
            return tracks
    except Exception as e:
        logger.warning(f"yt-data search error: {e}")
        return []


# ── Fallback 3: keyword / genre search ───────────────────────────────────────

async def _keyword_fallback(chat_id: int, msg, title: str, used_ids: set) -> Track | None:
    stop_words = {
        "the", "a", "an", "is", "in", "on", "and", "or", "of", "to",
        "tu", "hai", "ho", "hoon", "main", "mein", "ke", "ka", "ki",
        "se", "ne",
    }
    words = re.sub(r"[^\w\s]", "", title.lower()).split()
    keywords = [w for w in words if w not in stop_words and len(w) > 2]

    genre_queries = [
        "hindi romantic songs 2024",
        "best bollywood songs",
        "hindi hit songs playlist",
        "top indian songs 2024",
        "popular hindi songs",
    ]

    if keywords:
        kw = keywords[0]
        queries = [
            f"{kw} hindi songs",
            f"songs similar to {kw}",
            random.choice(genre_queries),
        ]
    else:
        queries = genre_queries[:3]

    track = None

    for search_query in queries:
        try:
            search = VideosSearch(search_query, limit=15)
            search_data = await search.next()
            results = (search_data or {}).get("result") or []
        except Exception as e:
            logger.error(f"AutoPlay search error: {e}")
            continue

        random.shuffle(results)

        for video in results:
            vid_link = video.get("link", "")
            vid_id = vid_link.split("v=")[-1].split("&")[0]
            vid_title = video.get("title", "").lower().strip()
            duration_str = video.get("duration") or "0:00"

            try:
                parts = duration_str.split(":")
                dur_sec = (
                    int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    if len(parts) == 3
                    else int(parts[0]) * 60 + int(parts[1])
                )
            except Exception:
                dur_sec = 0

            if not vid_id or vid_id in used_ids:
                continue
            if dur_sec > config.DURATION_LIMIT or dur_sec < 60:
                continue
            if any(kw in vid_title for kw in keywords[:2]):
                continue

            track = await yt.search(vid_link, msg.id, video=False)
            if track:
                break

        if track:
            break

    return track


async def try_autoplay(chat_id: int) -> bool:
    if not await db.is_autoplay(chat_id):
        return False

    title = _last_track.get(chat_id)
    if not title:
        return False

    _lang = await lang.get_lang(chat_id)
    used_ids = _used_ids.setdefault(chat_id, set())

    try:
        msg = await app.send_message(
            chat_id=chat_id,
            text=_lang["autoplay_searching"].format(title),
        )

        track = None
        keywords = _keyword_list(title)

        last_id = _last_track_id.get(chat_id)
        if last_id:
            track = _pick_related(chat_id, await _fetch_upnext(last_id), keywords)

        if not track:
            track = _pick_related(chat_id, await _fetch_related(title), keywords)

        if not track:
            track = await _keyword_fallback(chat_id, msg, title, used_ids)

        if not track:
            await msg.edit_text(_lang["autoplay_not_found"])
            return False

        track.user = app.name
        queue.add(chat_id, track)
        remember_track(chat_id, track.title, track.id, track.channel_name)

        if not track.file_path:
            await msg.edit_text(_lang["play_downloading"])
            track.file_path = await yt.download(
                track.id, video=False, duration=track.duration_sec
            )

        if not track.file_path:
            await msg.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            queue.clear(chat_id)
            return False

        track.message_id = msg.id
        await aero.play_media(chat_id=chat_id, message=msg, media=track)
        return True

    except Exception as e:
        logger.error(f"AutoPlay error in {chat_id}: {e}")
        return False


# ── Queue-end prompt ─────────────────────────────────────────────────────────

async def _queue_ended_prompt(chat_id: int) -> None:
    _lang = await lang.get_lang(chat_id)
    try:
        await app.send_message(
            chat_id=chat_id,
            text=_lang["autoplay_prompt"],
            reply_markup=buttons.autoplay_key(_lang["autoplay_btn"]),
        )
    except Exception as e:
        logger.warning(f"AutoPlay prompt error in {chat_id}: {e}")


# ── Patch aero.play_next ─────────────────────────────────────────────────────

_original_play_next = aero.play_next.__func__


async def _patched_play_next(self, chat_id: int) -> None:
    current = queue.get_current(chat_id)
    current_title = current.title if (current and hasattr(current, "title")) else None
    current_id = current.id if current else ""
    current_channel = current.channel_name if current else ""
    has_next = queue.get_next(chat_id, check=True) is not None

    await _original_play_next(self, chat_id)

    if current_title:
        remember_track(chat_id, current_title, current_id, current_channel)

    if not has_next and not await db.get_call(chat_id):
        if await db.is_autoplay(chat_id):
            await try_autoplay(chat_id)
        else:
            await _queue_ended_prompt(chat_id)


aero.play_next = _types.MethodType(_patched_play_next, aero)


# ── Autoplay button callback ─────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^autoplay$") & ~app.bl_users)
@lang.language()
@can_manage_vc
async def autoplay_cb(_, query: types.CallbackQuery):
    chat_id = query.message.chat.id
    await db.set_autoplay(chat_id, True)
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.answer(query.lang["autoplay_activated"], show_alert=False)
    await try_autoplay(chat_id)


# ── /autoplay command ────────────────────────────────────────────────────────

@app.on_message(
    filters.command(["autoplay", "ap"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
@can_manage_vc
async def autoplay_cmd(_, m: types.Message):
    chat_id = m.chat.id

    if len(m.command) < 2:
        state = await db.is_autoplay(chat_id)
        key = "autoplay_on" if state else "autoplay_off"
        return await m.reply_text(m.lang["autoplay_status"].format(m.lang[key]))

    sub = m.command[1].lower()

    if sub in ("on", "enable", "1", "true"):
        await db.set_autoplay(chat_id, True)
        return await m.reply_text(m.lang["autoplay_enabled"])

    if sub in ("off", "disable", "0", "false"):
        await db.set_autoplay(chat_id, False)
        return await m.reply_text(m.lang["autoplay_disabled"])

    return await m.reply_text(m.lang["autoplay_usage"])

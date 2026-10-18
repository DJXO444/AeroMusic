# Licensed under the MIT License.
# This file is part of AeroXMusic


import io
import os
import re
import time
import random
import asyncio
import aiohttp
from pathlib import Path

from py_yt import Playlist, VideosSearch

from aero import app, config, db, logger
from aero.helpers import Track, utils

# Circuit-breaker tuning for the primary streaming API.
FAILURE_THRESHOLD = 10
COOLDOWN = 24 * 60 * 60  # 24 hours in seconds


class YouTube:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.cookies = []
        self.checked = False
        self.cookie_dir = "aero/cookies"
        self.warned = False
        # Circuit-breaker state for the primary API.
        self._fail_count = 0
        self._disabled_until = 0.0
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )
        self.iregex = re.compile(
            r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)"
            r"(?!/(watch\?v=[A-Za-z0-9_-]{11}|shorts/[A-Za-z0-9_-]{11}"
            r"|playlist\?list=PL[A-Za-z0-9_-]+|[A-Za-z0-9_-]{11}))\S*"
        )

    def get_cookies(self):
        if not self.checked:
            for file in os.listdir(self.cookie_dir):
                if file.endswith(".txt"):
                    self.cookies.append(f"{self.cookie_dir}/{file}")
            self.checked = True
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; downloads might fail.")
            return None
        return random.choice(self.cookies)

    async def save_cookies(self, urls: list[str]) -> None:
        logger.info("Saving cookies from urls...")
        async with aiohttp.ClientSession() as session:
            for url in urls:
                name = url.split("/")[-1]
                link = "https://batbin.me/raw/" + name
                async with session.get(link) as resp:
                    resp.raise_for_status()
                    with open(f"{self.cookie_dir}/{name}.txt", "wb") as fw:
                        fw.write(await resp.read())
        logger.info(f"Cookies saved in {self.cookie_dir}.")

    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))

    def invalid(self, url: str) -> bool:
        return bool(re.match(self.iregex, url))

    async def search(self, query: str, m_id: int, video: bool = False) -> Track | None:
        logger.info("API → yt-search: %r", query)
        try:
            _search = VideosSearch(query, limit=1, with_live=False)
            results = await _search.next()
        except Exception as ex:
            logger.warning("yt-search failed for %r: %s", query, ex)
            return None
        if results and results["result"]:
            data = results["result"][0]
            logger.info("API ← yt-search: %s | %s", data.get("id"), (data.get("title") or "")[:40])
            return Track(
                id=data.get("id"),
                channel_name=data.get("channel", {}).get("name"),
                duration=data.get("duration"),
                duration_sec=utils.to_seconds(data.get("duration")),
                message_id=m_id,
                title=data.get("title")[:25],
                thumbnail=data.get("thumbnails", [{}])[-1].get("url").split("?")[0],
                url=data.get("link"),
                view_count=data.get("viewCount", {}).get("short"),
                video=video,
            )
        return None

    async def playlist(self, limit: int, user: str, url: str, video: bool) -> list[Track | None]:
        tracks = []
        logger.info("API → yt-playlist: %s (limit %d)", url, limit)
        try:
            plist = await Playlist.get(url)
            for data in plist["videos"][:limit]:
                track = Track(
                    id=data.get("id"),
                    channel_name=data.get("channel", {}).get("name", ""),
                    duration=data.get("duration"),
                    duration_sec=utils.to_seconds(data.get("duration")),
                    title=data.get("title")[:25],
                    thumbnail=data.get("thumbnails")[-1].get("url").split("?")[0],
                    url=data.get("link").split("&list=")[0],
                    user=user,
                    view_count="",
                    video=video,
                )
                tracks.append(track)
            logger.info("API ← yt-playlist: %d tracks", len(tracks))
        except Exception as ex:
            logger.warning("yt-playlist failed for %s: %s", url, ex)
        return tracks

    # ---------------------------------------------------------------- circuit breaker
    def _primary_up(self) -> bool:
        """Return True if the primary API is currently allowed. Auto-resets
        the breaker once the 24h cooldown has elapsed."""
        if self._disabled_until:
            if time.time() >= self._disabled_until:
                self._disabled_until = 0.0
                self._fail_count = 0
                logger.info("Primary YT API cooldown elapsed; re-enabled.")
            else:
                return False
        return True

    async def _trip_breaker(self) -> None:
        self._disabled_until = time.time() + COOLDOWN
        logger.error(
            "Primary YT API disabled for 24h after %d consecutive failures.",
            self._fail_count,
        )
        try:
            await app.send_message(
                config.LOGGER_ID,
                "<blockquote>⚠️ <b>Primary YouTube API disabled</b>\n\n"
                f"<b>Consecutive failures:</b> {self._fail_count}\n"
                "<b>Cooldown:</b> 24 hours\n"
                "<b>Routing:</b> Fallback API + cache</blockquote>",
            )
        except Exception as ex:
            logger.error("Could not send breaker alert to LOGGER_ID: %s", ex)

    async def _record_failure(self) -> None:
        self._fail_count += 1
        if self._fail_count >= FAILURE_THRESHOLD and not self._disabled_until:
            await self._trip_breaker()

    async def reset_primary(self) -> None:
        """Manual administrative reset of the primary API breaker."""
        self._disabled_until = 0.0
        self._fail_count = 0
        logger.info("Primary YT API manually reset.")

    # ---------------------------------------------------------------- primary stream
    async def _primary_stream(self, video_id: str, s_type: str) -> str | None:
        """Download the track from the primary API's proxy to a LOCAL file and
        return its path. We deliberately do NOT hand ffmpeg the live proxy URL:
        the proxy closes long connections after ~2-3 min, which ffmpeg reads as
        end-of-stream and the assistant leaves the call mid-song. A fully
        downloaded file plays to the end reliably (same as cached tracks)."""
        ext = "mp4" if s_type == "video" else "webm"
        path = f"downloads/{video_id}.{ext}"
        if Path(path).exists() and os.path.getsize(path) > 0:
            return path

        # Health-check the API first so the circuit breaker still sees failures.
        logger.info("API → primary-yt: stream %s (%s)", video_id, s_type)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{config.YTAPI_URL}/stream",
                    params={"query": video_id, "type": s_type},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Primary API returned HTTP %s for %s", resp.status, video_id
                        )
                        return None
        except asyncio.TimeoutError:
            logger.warning("Primary API timed out for %s", video_id)
            return None
        except aiohttp.ClientError as ex:
            logger.warning("Primary API request failed for %s: %s", video_id, ex)
            return None

        # Download the proxy stream to disk (streamed in chunks, no full RAM).
        proxy = f"{config.YTAPI_URL}/stream/{s_type}/{video_id}"
        tmp = f"{path}.part"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    proxy, timeout=aiohttp.ClientTimeout(total=600)
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Proxy stream HTTP %s for %s", resp.status, video_id)
                        return None
                    with open(tmp, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1 << 16):
                            f.write(chunk)
        except Exception as ex:
            logger.warning("Proxy download failed for %s: %s", video_id, ex)
            try:
                os.remove(tmp)
            except Exception:
                pass
            return None

        if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            logger.warning("Proxy download produced empty file for %s", video_id)
            return None
        os.replace(tmp, path)
        logger.info("API ← primary-yt: downloaded %s (%d bytes)", video_id, os.path.getsize(path))
        return path

    # ---------------------------------------------------------------- fallback pipeline
    async def _fallback_upload(self, video_id: str, s_type: str) -> str | None:
        """Fetch the file from the fallback API straight into RAM, upload it to
        the storage channel, and return the resulting Telegram file_id. No disk."""
        ext = "mp4" if s_type == "video" else "webm"
        logger.info("API → fallback-yt: download %s (%s)", video_id, s_type)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{config.FALLBACK_URL}/download",
                    params={
                        "url": video_id,
                        "type": s_type,
                        "api_key": config.FALLBACK_APIKEY,
                    },
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as resp:
                    if resp.status != 200:
                        logger.error(
                            "Fallback API returned HTTP %s for %s", resp.status, video_id
                        )
                        return None
                    body = await resp.read()
        except asyncio.TimeoutError:
            logger.error("Fallback API timed out for %s", video_id)
            return None
        except aiohttp.ClientError as ex:
            logger.error("Fallback API request failed for %s: %s", video_id, ex)
            return None

        if not body:
            logger.error("Fallback API returned an empty body for %s", video_id)
            return None

        logger.info("API ← fallback-yt: %s (%d bytes) → uploading to storage", video_id, len(body))
        buf = io.BytesIO(body)
        buf.name = f"{video_id}.{ext}"
        file_id = None
        try:
            sent = await app.send_document(
                config.STORAGE_CHANNEL,
                buf,
                caption=f"<code>{video_id}</code> | {s_type}",
            )
            file_id = sent.document.file_id
            logger.info("storage ← cached %s as file_id", video_id)
        except Exception as ex:
            logger.error("Failed to upload %s to storage channel: %s", video_id, ex)
        finally:
            buf.close()
        return file_id

    async def _resolve(self, file_id: str, video_id: str, s_type: str) -> str | None:
        """Materialise a Telegram file_id into a local path the mixer can stream."""
        ext = "mp4" if s_type == "video" else "webm"
        path = f"downloads/{video_id}.{ext}"
        if Path(path).exists():
            return path
        try:
            return await app.download_media(file_id, file_name=path)
        except Exception as ex:
            logger.error("Failed to fetch cached file %s: %s", file_id, ex)
            return None

    @staticmethod
    def cleanup(path) -> None:
        """Delete a transient downloaded track from disk. The permanent copy
        lives in the storage channel (file_id in Mongo), so this keeps the VPS
        disk from filling — the song re-fetches from Telegram next time."""
        if isinstance(path, str) and path.startswith("downloads/"):
            try:
                os.remove(path)
            except Exception:
                pass

    async def _persist(self, video_id: str, video: bool, path: str) -> None:
        """Upload a freshly-downloaded track to the storage channel and cache
        its file_id in Mongo, so every future play comes straight from Telegram
        (no re-download, no growing VPS disk). Runs in the background — never
        blocks playback."""
        if not config.STORAGE_CHANNEL:
            return
        if await db.get_yt_cache(video_id, video):
            return  # already cached by another concurrent play
        try:
            # Read the file into RAM now, so cleanup deleting it mid-song (a long
            # track finishing before the upload completes) can't corrupt this.
            ext = "mp4" if video else "webm"
            with open(path, "rb") as f:
                buf = io.BytesIO(f.read())
            buf.name = f"{video_id}.{ext}"
            size = buf.getbuffer().nbytes
            caption = (
                f"🎧 <b>{'Video' if video else 'Audio'}</b>\n"
                f"<b>ID:</b> <code>{video_id}</code>\n"
                f"<b>Link:</b> {self.base}{video_id}\n"
                f"<b>Size:</b> {size / 1048576:.2f} MB"
            )
            sent = await app.send_document(
                config.STORAGE_CHANNEL, buf, caption=caption
            )
            buf.close()
            file_id = sent.document.file_id
            await db.set_yt_cache(video_id, video, file_id)
            logger.info("storage ← cached %s (%d bytes)", video_id, size)
        except Exception as ex:
            logger.error("Failed to cache %s to storage: %s", video_id, ex)

    # ---------------------------------------------------------------- entry point
    async def download(
        self, video_id: str, video: bool = False, duration: int = 0
    ) -> str | None:
        # 1. Hard duration limit — reject before touching any API or DB.
        if duration and duration > config.DL_HARD_LIMIT:
            logger.info(
                "Rejected %s: %ss exceeds hard limit of %ss.",
                video_id, duration, config.DL_HARD_LIMIT,
            )
            return None

        s_type = "video" if video else "audio"

        # 2. Cache FIRST — durable Telegram storage. Any song played before comes
        #    straight from the storage channel file_id (fast, reliable local
        #    file), never re-downloaded.
        cached = await db.get_yt_cache(video_id, video)
        if cached:
            path = await self._resolve(cached, video_id, s_type)
            if path:
                logger.info("cache ← %s from storage", video_id)
                return path
            logger.warning("Cached file for %s unusable; re-fetching.", video_id)

        # 3. Not cached — download fresh from the primary API, then persist it to
        #    the storage channel + Mongo IN THE BACKGROUND so playback starts
        #    immediately (upload never blocks the song).
        if self._primary_up():
            path = await self._primary_stream(video_id, s_type)
            if path:
                self._fail_count = 0
                asyncio.create_task(self._persist(video_id, video, path))
                return path
            await self._record_failure()

        # 4. Primary down/failing — fallback RAM→channel pipeline (also persists).
        file_id = await self._fallback_upload(video_id, s_type)
        if not file_id:
            return None
        await db.set_yt_cache(video_id, video, file_id)
        return await self._resolve(file_id, video_id, s_type)

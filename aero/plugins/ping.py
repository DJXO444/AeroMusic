# Licensed under the MIT License.
# This file is part of AeroXMusic


import time
import psutil

from pyrogram import filters, types
from aero import app, aero, boot, config, lang
from aero.helpers import buttons


@app.on_message(filters.command(["alive", "ping"]) & ~app.bl_users)
@lang.language()
async def _ping(_, m: types.Message):
    start = time.time()
    sent = await m.reply_text(m.lang["pinging"])
    get_time = lambda s: (lambda r: (f"{r[-1]}, " if r[-1][:-4] != "0" else "") + ":".join(reversed(r[:-1])))([f"{v}{u}" for v, u in zip([s%60, (s//60)%60, (s//3600)%24, s//86400], ["s", "m", "h", "days"])])
    uptime = get_time(int(time.time() - boot))
    latency = round((time.time() - start) * 1000, 2)
    await sent.edit_media(
        media=types.InputMediaPhoto(
            media=config.PING_IMG,
            caption=m.lang["ping_pong"].format(
                latency,                             # {0} latency ms
                app.name,                            # {1} bot name
                uptime,                              # {2} uptime
                psutil.virtual_memory().percent,     # {3} ram %
                psutil.cpu_percent(interval=0),      # {4} cpu %
                psutil.disk_usage("/").percent,      # {5} disk %
                await aero.ping(),                   # {6} pytgcalls ms
            )
        ),
        reply_markup=buttons.ping_markup(m.lang["support"]),
    )

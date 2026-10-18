# Licensed under the MIT License.
# This file is part of AeroXMusic


import inspect

from pyrogram import enums, types

from aero import app, config, lang
from aero.core.lang import lang_codes

# Feature-detect the Bot API 9.4 button fields so we never crash on a kurigram
# build that lacks them — the styling just no-ops there.
_BTN_PARAMS = set(inspect.signature(types.InlineKeyboardButton.__init__).parameters)
_HAS_STYLE = "style" in _BTN_PARAMS
_HAS_EMOJI = "icon_custom_emoji_id" in _BTN_PARAMS
# kurigram compares style against the ButtonStyle enum, not a string.
_STYLE_MAP = {
    "primary": enums.ButtonStyle.PRIMARY,
    "success": enums.ButtonStyle.SUCCESS,
    "danger": enums.ButtonStyle.DANGER,
}


class Inline:
    def __init__(self):
        self.ikm = types.InlineKeyboardMarkup
        self.ikb = types.InlineKeyboardButton

    def btn(self, text, *, style=None, emoji_id=None, **kw):
        """InlineKeyboardButton with optional colour (style) and a premium
        emoji icon, applied only when the installed kurigram supports them."""
        if style and _HAS_STYLE:
            kw["style"] = _STYLE_MAP.get(style, style)
        if emoji_id and _HAS_EMOJI:
            kw["icon_custom_emoji_id"] = str(emoji_id)
        return self.ikb(text=text, **kw)

    def cancel_dl(self, text) -> types.InlineKeyboardMarkup:
        return self.ikm([[self.ikb(text=text, callback_data=f"cancel_dl")]])

    def controls(
        self,
        chat_id: int,
        status: str = None,
        timer: str = None,
        remove: bool = False,
    ) -> types.InlineKeyboardMarkup:
        keyboard = []
        if status:
            keyboard.append(
                [self.ikb(text=status, callback_data=f"controls status {chat_id}")]
            )
        elif timer:
            keyboard.append(
                [self.ikb(text=timer, callback_data=f"controls status {chat_id}")]
            )

        if not remove:
            keyboard.append(
                [
                    self.btn(text="▷", style="danger", callback_data=f"controls resume {chat_id}"),
                    self.btn(text="II", style="danger", callback_data=f"controls pause {chat_id}"),
                    self.btn(text="⥁", style="danger", callback_data=f"controls replay {chat_id}"),
                    self.btn(text="‣‣", style="danger", callback_data=f"controls skip {chat_id}"),
                    self.btn(text="▢", style="danger", callback_data=f"controls stop {chat_id}"),
                ]
            )
        return self.ikm(keyboard)

    # Commands sub-menu: 6 categories in a 2x3 grid.
    cmd_cats = ["admin", "vctool", "group", "config", "features", "discovery"]
    cmd_labels = {
        "admin": "𝖠ᴅᴍɪɴ",
        "vctool": "𝖵ᴄ 𝖳ᴏᴏʟ",
        "group": "𝖦ʀᴏᴜᴘ",
        "config": "𝖢ᴏɴꜰɪɢ",
        "features": "𝖥ᴇᴀᴛᴜʀᴇs",
        "discovery": "𝖣ɪsᴄᴏᴠᴇʀʏ",
    }
    cmd_emojis = {
        "admin": "5310224206732996002",
        "vctool": "5222472119295684375",
        "group": "5219672809936006424",
        "config": "5116113383128564448",
        "features": "4915853119839011973",
        "discovery": "5258389638006984449",
    }
    CLOSE_EMOJI = "4915853119839011973"
    BACK_EMOJI = "5244820603663296299"
    PLAY_NOW_EMOJI = "5172447776205702031"

    def commands_menu(self, _lang: dict) -> types.InlineKeyboardMarkup:
        cats = self.cmd_cats

        def cat_btn(c):
            return self.btn(
                text=self.cmd_labels[c],
                style="success",
                emoji_id=self.cmd_emojis[c],
                callback_data=f"help {c}",
            )

        # 6 categories in 3 rows of 2.
        rows = [
            [cat_btn(cats[i]), cat_btn(cats[i + 1])]
            for i in range(0, len(cats), 2)
        ]
        rows.append(
            [
                self.btn(text=_lang["back"], style="success", emoji_id=self.BACK_EMOJI, callback_data="help start"),
                self.btn(text=_lang["close"], style="success", emoji_id=self.CLOSE_EMOJI, callback_data="help close"),
            ]
        )
        return self.ikm(rows)

    def help_page(self, _lang: dict, category: str) -> types.InlineKeyboardMarkup:
        cats = self.cmd_cats
        i = cats.index(category)
        return self.ikm(
            [
                [
                    self.btn(text="◁", style="success", callback_data=f"help {cats[(i - 1) % len(cats)]}"),
                    self.btn(text=_lang["back"], style="success", emoji_id=self.BACK_EMOJI, callback_data="help menu"),
                    self.btn(text="▷", style="success", callback_data=f"help {cats[(i + 1) % len(cats)]}"),
                ],
                [self.btn(text=_lang["close"], style="success", emoji_id=self.CLOSE_EMOJI, callback_data="help close")],
            ]
        )

    def lang_markup(self, _lang: str) -> types.InlineKeyboardMarkup:
        langs = lang.get_languages()

        buttons = [
            self.ikb(
                text=f"{name} ({code}) {'✔️' if code == _lang else ''}",
                callback_data=f"lang_change {code}",
            )
            for code, name in langs.items()
        ]
        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        return self.ikm(rows)

    def ping_markup(self, text: str) -> types.InlineKeyboardMarkup:
        return self.ikm([[self.ikb(text=text, url=config.SUPPORT_CHAT)]])

    def play_queued(
        self, chat_id: int, item_id: str, _text: str
    ) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.btn(
                        text=_text, style="danger", emoji_id=self.PLAY_NOW_EMOJI,
                        callback_data=f"controls force {chat_id} {item_id}"
                    )
                ]
            ]
        )

    def autoplay_key(self, text: str) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [[self.btn(text=text, style="danger", emoji_id="5897854227648090069", callback_data="autoplay")]]
        )

    def queue_markup(
        self, chat_id: int, _text: str, playing: bool
    ) -> types.InlineKeyboardMarkup:
        _action = "pause" if playing else "resume"
        return self.ikm(
            [[self.btn(text=_text, style="danger", callback_data=f"controls {_action} {chat_id} q")]]
        )

    def settings_markup(
        self, lang: dict, admin_only: bool, cmd_delete: bool, language: str, chat_id: int
    ) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(
                        text=lang["play_mode"] + " ➜",
                        callback_data="settings",
                    ),
                    self.ikb(text=admin_only, callback_data="settings play"),
                ],
                [
                    self.ikb(
                        text=lang["cmd_delete"] + " ➜",
                        callback_data="settings",
                    ),
                    self.ikb(text=cmd_delete, callback_data="settings delete"),
                ],
                [
                    self.ikb(
                        text=lang["language"] + " ➜",
                        callback_data="settings",
                    ),
                    self.ikb(text=lang_codes[language], callback_data="language"),
                ],
            ]
        )

    def start_key(
        self, lang: dict, private: bool = False
    ) -> types.InlineKeyboardMarkup:
        add_url = f"https://t.me/{app.username}?startgroup=true"
        if not private:
            # Group start: just Add Me | Support.
            return self.ikm(
                [
                    [
                        self.btn(text=lang["add_me"], style="success", emoji_id="5197667875707446017", url=add_url),
                        self.btn(text=lang["support"], style="success", emoji_id="5253665397319683502", url=config.SUPPORT_CHAT),
                    ]
                ]
            )
        # PM start: full menu.
        return self.ikm(
            [
                [self.btn(text=lang["commands"], style="success", emoji_id="5219943216781995020", callback_data="help menu")],
                [
                    self.btn(text=lang["support"], style="success", emoji_id="5253665397319683502", url=config.SUPPORT_CHAT),
                    self.btn(text=lang["channel"], style="success", emoji_id="5194968578956222329", url=config.SUPPORT_CHANNEL),
                ],
                [self.btn(text=lang["add_me"], style="success", emoji_id="5197667875707446017", url=add_url)],
            ]
        )

    def vc_join(self, text: str, url: str) -> types.InlineKeyboardMarkup:
        return self.ikm([[self.btn(text=text, style="success", emoji_id="5134202243486057363", url=url)]])

    def vc_join_blank(self, text: str) -> types.InlineKeyboardMarkup:
        # Visible button with no destination (Telegram forbids empty URLs, so
        # this is a no-op callback button instead).
        return self.ikm([[self.btn(text=text, style="success", emoji_id="5134202243486057363", callback_data="vc_noop")]])

    def welcome_key(self, text: str) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [[self.btn(text=text, style="success", emoji_id="5222108309795908493", url=f"https://t.me/{app.username}?startgroup=true")]]
        )

    def help_pm(self, text: str) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [[self.btn(text=text, style="success", emoji_id="6147943438785449262", url=f"https://t.me/{app.username}?start=help")]]
        )

    def yt_key(self, link: str) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(text="❐", copy_text=link),
                    self.ikb(text="Youtube", url=link),
                ],
            ]
        )

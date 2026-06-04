# Copyright © 2018 The GNOME Music Developers
#
# GNOME Music is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# GNOME Music is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with GNOME Music; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# The GNOME Music authors hereby grant permission for non-GPL compatible
# GStreamer plugins to be used and distributed together with GStreamer
# and GNOME Music.  This permission is above and beyond the permissions
# granted by the GPL license by which GNOME Music is covered.  If you
# modify this code, you may extend this exception to your version of the
# code, but you are not obligated to do so.  If you do not wish to do so,
# delete this exception statement from your version.

import json
import re
import subprocess
import threading
from gettext import gettext as _
from gi.repository import Gio, GLib, GObject, Gtk

from gnomemusic.defaulticon import DefaultIcon
from gnomemusic.gstplayer import Playback
from gnomemusic.utils import ArtSize
from gnomemusic.player import Player, RepeatMode
from gnomemusic.widgets.smoothscale import SmoothScale  # noqa: F401
from gnomemusic.widgets.twolinetip import TwoLineTip
import gnomemusic.utils as utils


@Gtk.Template(resource_path='/org/gnome/Music/ui/PlayerToolbar.ui')
class PlayerToolbar(Gtk.ActionBar):
    """Main Player widget object

    Contains the ui of playing a song with Music.
    """

    __gtype_name__ = 'PlayerToolbar'

    _artist_label = Gtk.Template.Child()
    _art_stack = Gtk.Template.Child()
    _buttons_and_scale = Gtk.Template.Child()
    _duration_label = Gtk.Template.Child()
    _next_button = Gtk.Template.Child()
    _pause_image = Gtk.Template.Child()
    _play_button = Gtk.Template.Child()
    _play_image = Gtk.Template.Child()
    _prev_button = Gtk.Template.Child()
    _progress_scale = Gtk.Template.Child()
    _progress_time_label = Gtk.Template.Child()
    _repeat_menu_button = Gtk.Template.Child()
    _repeat_image = Gtk.Template.Child()
    _song_info_box = Gtk.Template.Child()
    _title_label = Gtk.Template.Child()
    _lyrics_stack = Gtk.Template.Child()
    _lyrics_toggle_button = Gtk.Template.Child()

    def __init__(self):
        super().__init__()

        self._player = None
        self._current_lyrics = []
        self._current_lyric_index = -1
        self._lyrics_timer_id = 0
        self._lyrics_enabled = False

        self._art_stack.props.size = ArtSize.SMALL
        self._art_stack.props.art_type = DefaultIcon.Type.ALBUM

        self._tooltip = TwoLineTip()

        # A centered widget has an expand child property set to False
        # by default. It needs to be True to have a progress scale
        # at the correct size.
        main_container = self._buttons_and_scale.get_parent()
        main_container.child_set_property(
            self._buttons_and_scale, "expand", True)

        repeat_menu = Gio.Menu.new()
        for mode in RepeatMode:
            item = Gio.MenuItem.new()
            item.set_label(mode.label)
            item.set_action_and_target_value(
                "playertoolbar.repeat", GLib.Variant("s", str(mode.value)))
            repeat_menu.append_item(item)

        self._repeat_menu_button.props.menu_model = repeat_menu
        self._repeat_action: Gio.SimpleAction = Gio.SimpleAction.new_stateful(
            "repeat", GLib.VariantType.new("s"), GLib.Variant("s", ""))

    # FIXME: This is a workaround for not being able to pass the player
    # object via init when using Gtk.Builder.
    @GObject.Property(type=Player, default=None)
    def player(self):
        """The GstPlayer object used

        :return: player object
        :rtype: GstPlayer
        """
        return self._player

    @player.setter  # type: ignore
    def player(self, player):
        """Set the GstPlayer object used

        :param GstPlayer player: The GstPlayer to use
        """
        if (player is None
                or (self._player is not None
                    and self._player != player)):
            return

        self._player = player
        self._progress_scale.props.player = self._player

        self._player.connect('song-changed', self._update_view)
        self._player.connect(
            'notify::repeat-mode', self._on_repeat_mode_changed)
        self._player.connect('notify::state', self._sync_playing)
        self._player.connect('seek-finished', self._on_seek_finished)

        repeat_mode = self._player.props.repeat_mode
        self._repeat_action.set_state(
            GLib.Variant("s", str(repeat_mode.value)))
        self._repeat_action.connect("activate", self._repeat_menu_changed)
        action_group = Gio.SimpleActionGroup()
        action_group.add_action(self._repeat_action)
        self.insert_action_group("playertoolbar", action_group)

        self._sync_repeat_image()

    def _repeat_menu_changed(
            self, action: Gio.SimpleAction, new_state: GLib.Variant) -> None:
        self._repeat_action.set_state(new_state)
        new_mode = new_state.get_string()
        self._player.props.repeat_mode = RepeatMode(int(new_mode))
        self._repeat_menu_button.props.active = False

    @Gtk.Template.Callback()
    def _on_progress_value_changed(self, progress_scale):
        seconds = int(progress_scale.get_value() / 60)
        self._progress_time_label.set_label(utils.seconds_to_string(seconds))

    @Gtk.Template.Callback()
    def _on_prev_button_clicked(self, button):
        self._player.previous()

    @Gtk.Template.Callback()
    def _on_play_button_clicked(self, button):
        self._player.play_pause()

    @Gtk.Template.Callback()
    def _on_next_button_clicked(self, button):
        self._player.next()

    def _on_repeat_mode_changed(self, klass, param):
        self._sync_repeat_image()
        self._sync_prev_next()

    def _sync_repeat_image(self) -> None:
        self._repeat_image.set_from_icon_name(
            self._player.props.repeat_mode.icon, Gtk.IconSize.MENU)

    def _sync_playing(self, player, state):
        if (self._player.props.state == Playback.STOPPED
                and not self._player.props.has_next
                and not self._player.props.has_previous):
            self.hide()
            return

        self.show()

        if self._player.props.state == Playback.PLAYING:
            image = self._pause_image
            tooltip = _("Pause")
        else:
            image = self._play_image
            tooltip = _("Play")

        if self._play_button.get_image() != image:
            self._play_button.set_image(image)

        self._play_button.set_tooltip_text(tooltip)

        if self._player.props.state == Playback.PLAYING:
            self._start_lyrics_timer()
        else:
            self._stop_lyrics_timer()

    def _sync_prev_next(self):
        self._next_button.props.sensitive = self._player.props.has_next
        self._prev_button.props.sensitive = self._player.props.has_previous

    def _update_view(self, player):
        """Update all visual elements on song change

        :param Player player: The main player object
        """
        coresong = player.props.current_song
        self._duration_label.props.label = utils.seconds_to_string(
            coresong.props.duration)
        self._progress_time_label.props.label = "0:00"

        self._play_button.set_sensitive(True)
        self._sync_prev_next()

        artist = coresong.props.artist
        title = coresong.props.title

        self._title_label.props.label = title
        self._artist_label.props.label = artist

        self._tooltip.props.title = title
        self._tooltip.props.subtitle = artist

        self._art_stack.props.coreobject = coresong
        self._load_lyrics_async(coresong)

    @Gtk.Template.Callback()
    def _on_tooltip_query(self, widget, x, y, kb, tooltip, data=None):
        tooltip.set_custom(self._tooltip)

        return True

    def _extract_lyrics(self, path):
        try:
            res = subprocess.run([
                'ffprobe', '-show_entries', 'format_tags', '-of', 'json', '-v', 'quiet', path
            ], capture_output=True, text=True, check=True)
            data = json.loads(res.stdout)
            tags = data.get('format', {}).get('tags', {})
            for key, val in tags.items():
                if key.lower() in ('lyrics', 'sylt', 'unsyncedlyrics', 'unsynced lyrics'):
                    return val
            return None
        except Exception as e:
            return None

    def _parse_lrc(self, lyrics_str):
        lines = lyrics_str.splitlines()
        parsed = []
        for line in lines:
            line = line.strip()
            matches = re.findall(r'\[(\d+):(\d+(?:\.\d+)?)\]', line)
            if not matches:
                continue
            text = re.sub(r'\[\d+:\d+(?:\.\d+)?\]', '', line).strip()
            for m in matches:
                minutes = int(m[0])
                seconds = float(m[1])
                time_in_seconds = minutes * 60 + seconds
                parsed.append((time_in_seconds, text))
        parsed.sort(key=lambda x: x[0])
        return parsed

    def _load_lyrics_async(self, coresong):
        self._current_lyrics = []
        self._current_lyric_index = -1
        self._set_lyric_text("")

        if not coresong:
            return

        url = coresong.props.url
        if not url or not url.startswith("file://"):
            return

        def worker():
            try:
                path, _ = GLib.filename_from_uri(url)
                lyrics_str = self._extract_lyrics(path)
                if lyrics_str:
                    parsed_lyrics = self._parse_lrc(lyrics_str)
                    GLib.idle_add(self._on_lyrics_loaded, coresong, parsed_lyrics)
            except Exception as e:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_lyrics_loaded(self, coresong, parsed_lyrics):
        current = self._player.props.current_song if self._player else None
        if current == coresong:
            self._current_lyrics = parsed_lyrics
            if self._lyrics_enabled:
                self._update_lyrics_timer()

    def _start_lyrics_timer(self):
        if self._lyrics_enabled and self._lyrics_timer_id == 0:
            self._lyrics_timer_id = GLib.timeout_add(100, self._update_lyrics_timer)

    def _stop_lyrics_timer(self):
        if self._lyrics_timer_id != 0:
            GLib.source_remove(self._lyrics_timer_id)
            self._lyrics_timer_id = 0

    def _update_lyrics_timer(self):
        if not self._player:
            self._lyrics_timer_id = 0
            return False

        if self._player.props.state != Playback.PLAYING:
            self._lyrics_timer_id = 0
            return False

        pos = self._player.get_position()
        idx = self._get_lyric_index(pos)
        if idx != self._current_lyric_index:
            self._current_lyric_index = idx
            text = self._current_lyrics[idx][1] if idx != -1 else ""
            self._set_lyric_text(text)

        return True

    def _get_lyric_index(self, pos):
        if not self._current_lyrics:
            return -1
        for i in range(len(self._current_lyrics) - 1, -1, -1):
            if self._current_lyrics[i][0] <= pos:
                return i
        return -1

    def _set_lyric_text(self, text):
        current_child = self._lyrics_stack.get_visible_child()
        current_text = current_child.get_text() if isinstance(current_child, Gtk.Label) else ""

        if text == "" or current_text == "":
            self._lyrics_stack.props.transition_type = Gtk.StackTransitionType.CROSSFADE
        else:
            self._lyrics_stack.props.transition_type = Gtk.StackTransitionType.SLIDE_UP

        new_label = Gtk.Label(label=text)
        new_label.get_style_context().add_class("lyrics-label")
        new_label.props.wrap = True
        new_label.props.halign = Gtk.Align.CENTER
        new_label.props.valign = Gtk.Align.CENTER
        new_label.show()

        self._lyrics_stack.add(new_label)
        self._lyrics_stack.set_visible_child(new_label)

        GLib.timeout_add(500, self._cleanup_old_lyrics, new_label)

    def _cleanup_old_lyrics(self, current_label):
        for child in self._lyrics_stack.get_children():
            if child != current_label:
                self._lyrics_stack.remove(child)
                child.destroy()
        return False

    @Gtk.Template.Callback()
    def _on_lyrics_toggle_toggled(self, button):
        self._lyrics_enabled = button.get_active()
        self._lyrics_stack.set_visible(self._lyrics_enabled)
        if self._lyrics_enabled:
            self._start_lyrics_timer()
            pos = self._player.get_position() if self._player else 0.0
            idx = self._get_lyric_index(pos)
            text = self._current_lyrics[idx][1] if idx != -1 else ""
            self._set_lyric_text(text)
        else:
            self._stop_lyrics_timer()

    def _on_seek_finished(self, player):
        if self._lyrics_enabled:
            pos = self._player.get_position()
            idx = self._get_lyric_index(pos)
            if idx != self._current_lyric_index:
                self._current_lyric_index = idx
                text = self._current_lyrics[idx][1] if idx != -1 else ""
                self._set_lyric_text(text)

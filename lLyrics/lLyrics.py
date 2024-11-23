# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import re
import os
import threading
import webbrowser
import sys
import unicodedata

from threading import Thread

from gi.repository import GObject
from gi.repository import Peas
from gi.repository import Gdk
from gi.repository import RB
from gi.repository import Gtk
from gi.repository import Pango
from gi.repository import GdkPixbuf
from gi.repository import GLib
from gi.repository import Gio

import ChartlyricsParser
import LyricwikiParser
import MetrolyricsParser
import LetrasTerraParser
import AZLyricsParser
import LyricsmaniaParser
import DarklyricsParser
import GeniusParser
import VagalumeParser
import Util

from lLyrics_rb3compat import ActionGroup
from lLyrics_rb3compat import ApplicationShell

from Config import Config
from Config import ConfigDialog

import gettext
from tinytag import TinyTag

gettext.install('lLyrics', os.path.dirname(__file__) + "/locale/")

view_menu_ui = """
<ui>
    <menubar name="MenuBar">
        <menu name="ViewMenu" action="View">
            <menuitem name="lLyrics" action="ToggleLyricSideBar" />
        </menu>
    </menubar>
</ui>
"""

context_ui = """
<ui>
    <popup name="BrowserSourceViewPopup">
        <placeholder name="PluginPlaceholder">
            <menuitem name="lLyricsPopup" action="lLyricsPopupAction"/>
        </placeholder>
      </popup>
 
    <popup name="PlaylistViewPopup">
        <placeholder name="PluginPlaceholder">
            <menuitem name="lLyricsPopup" action="lLyricsPopupAction"/>
        </placeholder>
    </popup>
 
    <popup name="QueuePlaylistViewPopup">
        <placeholder name="PluginPlaceholder">
            <menuitem name="lLyricsPopup" action="lLyricsPopupAction"/>
        </placeholder>
    </popup>
     
    <popup name="PodcastViewPopup">
        <placeholder name="PluginPlaceholder">
            <menuitem name="lLyricsPopup" action="lLyricsPopupAction"/>
        </placeholder>
    </popup>
</ui>
"""

LYRICS_TITLE_STRIP = ["\(live[^\)]*\)", "\(acoustic[^\)]*\)", "\([^\)]*mix\)", "\([^\)]*version\)", "\([^\)]*edit\)",
                      "\(feat[^\)]*\)", "\([^\)]*bonus[^\)]*track[^\)]*\)"]
LYRICS_TITLE_REPLACE = [("/", "-"), (" & ", " and ")]
LYRICS_ARTIST_REPLACE = [("/", "-"), (" & ", " and ")]

LYRICS_SOURCES = ["Lyricwiki.org", "Letras.terra.com.br", "Metrolyrics.com", "AZLyrics.com", "Lyricsmania.com",
                  "Vagalume.com.br", "Genius.com", "Darklyrics.com", "Chartlyrics.com"]


class lLyrics(GObject.Object, Peas.Activatable):
    __gtype_name__ = 'lLyrics'
    object = GObject.property(type=GObject.Object)

    def __init__(self):
        GObject.Object.__init__(self)
        GObject.threads_init()
        Gdk.threads_init()

    def do_activate(self):
        # Get references for the Shell and the Shell-player
        self.shell = self.object
        self.player = self.shell.props.shell_player
        self.appshell = ApplicationShell(self.shell)

        # Create dictionary which assigns sources to their corresponding modules
        self.dict = dict({"Lyricwiki.org": LyricwikiParser, "Letras.terra.com.br": LetrasTerraParser,
                          "Metrolyrics.com": MetrolyricsParser, "AZLyrics.com": AZLyricsParser,
                          "Lyricsmania.com": LyricsmaniaParser, "Chartlyrics.com": ChartlyricsParser,
                          "Darklyrics.com": DarklyricsParser, "Genius.com": GeniusParser,
                          "Vagalume.com.br": VagalumeParser})
        self.add_builtin_lyrics_sources()

        # Get the user preferences
        config = Config()
        self.settings = config.get_settings()
        self.get_user_preferences(self.settings, None, config)
        # Watch for setting changes
        self.skc_id = self.settings.connect('changed', self.get_user_preferences, config)

        self.position = RB.ShellUILocation.RIGHT_SIDEBAR
        if self.left_sidebar:
            self.position = RB.ShellUILocation.SIDEBAR

        # Initialize the UI
        self.lmui_id = None
        self.tbui_id = None
        self.init_sidebar()
        self.init_menu()

        # Create flag, used to pop out sidebar on initial start of playback
        self.first = True
        self.visible = False

        # Event flag indicates whether the user is currently editing lyrics
        self.edit_event = threading.Event()
        self.edit_event.set()

        self.current_source = None
        self.tags = None
        self.current_tag = None
        self.showing_on_demand = False
        self.was_corrected = False

        # Search lyrics if already playing (this will be the case if user reactivates plugin during playback)
        if self.player.props.playing:
            self.search_lyrics(self.player, self.player.get_playing_entry())
        # Search lyrics everytime the song changes 
        self.psc_id = self.player.connect('playing-song-changed', self.search_lyrics)
        # Connect to elapsed-changed signal to handle synchronized lyrics
        self.pec_id = self.player.connect('elapsed-changed', self.elapsed_changed)

        print("activated plugin lLyrics")

    def do_deactivate(self):
        if self.visible:
            self.shell.remove_widget(self.vbox, self.position)

        self.settings.disconnect(self.skc_id)
        if self.psc_id is not None:
            self.player.disconnect(self.psc_id)
            self.player.disconnect(self.pec_id)

        self.appshell.cleanup()

        self.vbox = None
        self.textbuffer = None
        self.textview = None
        self.psc_id = None
        self.visible = None
        self.player = None
        self.toggle_action_group = None
        self.context_action_group = None
        self.appshell = None
        self.menu = None
        self.button_menu = None
        self.cache = None
        self.dict = None
        self.sources = None
        self.ui_id = None
        self.tag = None
        self.first = None
        self.current_source = None
        self.artist = None
        self.filepath = None
        self.title = None
        self.clean_artist = None
        self.clean_title = None
        self.path = None
        self.lyrics_before_edit = None
        self.edit_event = None
        self.path_before_edit = None
        self.sources = None
        self.cache = None
        self.lyrics_folder = None
        self.ignore_brackets = None
        self.left_sidebar = None
        self.hide_label = None
        self.show_first = None
        self.position = None
        self.hbox = None
        self.back_button = None

        self.shell = None

        print("deactivated plugin lLyrics")

    def add_builtin_lyrics_sources(self):
        # find and append path for built-in lyrics plugin
        for p in sys.path:
            if p.endswith("/rhythmbox/plugins/rb"):
                path = p.replace("/rb", "/lyrics")
                sys.path.append(path)
                break
        else:
            print("Path to built-in lyrics plugin could not be detected")
            return

    def get_user_preferences(self, settings, key, config):
        self.sources = config.get_lyrics_sources()
        self.show_first = config.get_show_first()
        self.cache = config.get_cache_lyrics()
        self.lyrics_folder = config.get_lyrics_folder()
        self.ignore_brackets = config.get_ignore_brackets()
        self.left_sidebar = config.get_left_sidebar()
        self.hide_label = config.get_hide_label()

        # if this is called in do_activate or we need a reload to apply, return here
        if key is None or key in ["left-sidebar"]:
            return

        if key == "hide-label":
            if self.hide_label:
                self.label.hide()
            else:
                self.label.show()
            return

    def insert_ui(self):
        self.appshell.add_app_menuitems(view_menu_ui, 'lLyricsPluginToggleActions', 'view')
        self.appshell.add_browser_menuitems(context_ui, 'lLyricsPluginPopupActions')

    def init_menu(self):
        # add actions
        self.toggle_action_group = ActionGroup(self.shell, 'lLyricsPluginToggleActions')
        self.toggle_action_group.add_action(func=self.toggle_visibility,
                                            action_name='ToggleLyricSideBar', label=_("Lyrics"),
                                            action_state=ActionGroup.TOGGLE,
                                            action_type='app', accel="<Ctrl>l",
                                            tooltip=_("Display lyrics for the current playing song"))
        self.appshell.insert_action_group(self.toggle_action_group)

        self.context_action_group = ActionGroup(self.shell, 'lLyricsPluginPopupActions')
        self.context_action_group.add_action(action_name="lLyricsPopupAction", label=_("Show lyrics"),
                                             tooltip=_("Search and display lyrics for this song"),
                                             func=self.context_action_callback)
        self.appshell.insert_action_group(self.context_action_group)

        self.insert_ui()

    def init_sidebar(self):
        self.vbox = Gtk.VBox()

        hbox_header = Gtk.HBox()

        self.label = Gtk.Label(_("Lyrics"))
        self.label.set_use_markup(True)
        self.label.set_padding(3, 10)
        self.label.set_alignment(0, 0)

        self.menu = self.get_button_menu()
        self.set_menu_sensitive(False)

        # menu without toolbar
        icon_factory = Gtk.IconFactory()
        pxbf = GdkPixbuf.Pixbuf.new_from_file(os.path.dirname(__file__) + "/menu-icon.png")
        icon_factory.add("llyrics_menu", Gtk.IconSet.new_from_pixbuf(pxbf))
        icon_factory.add_default()

        menu_button = Gtk.Image.new_from_stock("llyrics_menu", Gtk.IconSize.SMALL_TOOLBAR)
        event_box = Gtk.EventBox()
        event_box.add(menu_button)
        event_box.connect("button-press-event", self.popup_menu, self.menu)

        hbox_header.pack_start(self.label, True, True, 0)
        hbox_header.pack_end(event_box, False, False, 5)

        # create a TextView for displaying lyrics
        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_left_margin(10)
        self.textview.set_right_margin(10)
        self.textview.set_pixels_above_lines(5)
        self.textview.set_pixels_below_lines(5)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)

        # create a ScrollView
        sw = Gtk.ScrolledWindow()
        sw.add(self.textview)
        sw.set_shadow_type(Gtk.ShadowType.IN)

        # initialize a TextBuffer to store lyrics in
        self.textbuffer = Gtk.TextBuffer()
        self.textview.set_buffer(self.textbuffer)

        # tag to style headers bold and underlined
        self.tag = self.textbuffer.create_tag(None, underline=Pango.Underline.SINGLE, weight=600,
                                              pixels_above_lines=10, pixels_below_lines=20)
        # tag to highlight synchronized lyrics
        self.sync_tag = self.textbuffer.create_tag(None, weight=600)

        # create save and cancel buttons for edited lyrics
        save_button = Gtk.Button.new_with_label(_("Save"))
        save_button.connect("clicked", self.save_button_callback)
        cancel_button = Gtk.Button.new_with_label(_("Cancel"))
        cancel_button.connect("clicked", self.cancel_button_callback)
        self.hbox = Gtk.HBox()
        self.hbox.pack_start(save_button, True, True, 3)
        self.hbox.pack_start(cancel_button, True, True, 3)

        # button for closing on demand lyrics
        self.back_button = Gtk.Button.new_with_label(_("Back to playing song"))
        self.back_button.connect("clicked", self.back_button_callback)

        # pack everything into side pane
        self.vbox.pack_start(hbox_header, False, False, 0)
        self.vbox.pack_start(sw, True, True, 0)
        self.vbox.pack_end(self.hbox, False, False, 3)
        self.vbox.pack_end(self.back_button, False, False, 3)

        self.vbox.show_all()
        self.hbox.hide()
        self.back_button.hide()

        if self.hide_label:
            self.label.hide()

        self.visible = False

    def get_button_menu(self):
        menu = Gtk.Menu()

        self.radio_sources = Gtk.Menu()

        item_unselect = Gtk.RadioMenuItem.new_with_label([], "SelectNothing")
        item_unselect.connect("activate", self.scan_selected_source_callback, "SelectNothing")
        self.radio_sources.append(item_unselect)

        last_item = item_unselect

        for entry in LYRICS_SOURCES[:-1]:
            last_item = self.add_radio_menu_item(self.radio_sources, entry, self.scan_selected_source_callback,
                                                 last_item)

        self.radio_sources.append(Gtk.SeparatorMenuItem())
        self.add_radio_menu_item(self.radio_sources, _("From cache file"), self.scan_selected_source_callback,
                                 last_item)

        self.radio_sources.show_all()

        item_sources = Gtk.MenuItem(_("Sources"))
        item_sources.set_submenu(self.radio_sources)
        menu.append(item_sources)

        self.add_menu_item(menu, _("Scan next source"), self.scan_next_action_callback)
        self.add_menu_item(menu, _("Scan all sources"), self.scan_all_action_callback)
        menu.append(Gtk.SeparatorMenuItem())
        self.add_menu_item(menu, _("Search online"), self.search_online_action_callback)
        menu.append(Gtk.SeparatorMenuItem())
        self.add_menu_item(menu, _("Mark as instrumental"), self.instrumental_action_callback)
        menu.append(Gtk.SeparatorMenuItem())
        self.add_menu_item(menu, _("Clear lyrics"), self.clear_action_callback)
        self.add_menu_item(menu, _("Edit lyrics"), self.edit_action_callback)

        # add preferences item
        menu.append(Gtk.SeparatorMenuItem())
        self.add_menu_item(menu, _("Preferences"), self.preferences_dialog_action_callback)

        menu.show_all()

        # hide the SelectNothing choice
        item_unselect.hide()

        return menu

    def add_menu_item(self, menu, label, callback):
        item = Gtk.MenuItem(label)
        item.connect("activate", callback)
        menu.append(item)

    def add_radio_menu_item(self, menu, label, callback, last):
        group = last.get_group()
        item = Gtk.RadioMenuItem.new_with_label(group, label)
        if label == _("From cache file"):
            label = "From cache file"
        item.connect("toggled", callback, label)
        menu.append(item)

        return item

    def set_menu_sensitive(self, sensitive):
        index = 0
        for item in self.menu:
            # 'Preferences' option should always be sensitive
            if index == len(self.menu) - 1:
                continue
            item.set_sensitive(sensitive)
            index += 1

    def set_radio_menu_item_active(self, itemlabel):
        for item in self.radio_sources:
            if item.get_label() == itemlabel:
                item.set_active(True)
                break

    def popup_menu(self, widget, event, menu):
        menu.popup(None, widget, None, None, event.button, event.time)

    def toggle_visibility(self, action, param=None, data=None):
        action = self.toggle_action_group.get_action('ToggleLyricSideBar')

        if action.get_active():
            self.shell.add_widget(self.vbox, self.position, True, True)
            self.visible = True
            if not self.first and not self.showing_on_demand:
                self.search_lyrics(self.player, self.player.get_playing_entry())
        else:
            self.shell.remove_widget(self.vbox, self.position)
            self.visible = False

    def search_lyrics(self, player, entry):
        # clear sync stuff
        if self.tags is not None:
            self.tags = None
            self.current_tag = None
            start, end = self.textbuffer.get_bounds()
            self.textbuffer.remove_tag(self.sync_tag, start, end)

        if entry is None:
            return

        # don't show lyrics for podcasts and radio
        if entry.get_entry_type().get_name() in ('iradio', 'podcast-post'):
            print("entry type: " + entry.get_entry_type().get_name())
            if not self.first:
                self.first = True
                print('removing the sidebar')
                self.toggle_action_group.get_action("ToggleLyricSideBar").set_active(False)
            return

        # pop out sidebar at first playback
        if self.first and not self.showing_on_demand:
            self.first = False
            if not self.visible and self.show_first:
                self.toggle_action_group.get_action("ToggleLyricSideBar").set_active(True)
                # toggling the sidebar will start lyrics search again, so we can return here
                return

        # only do something if visible
        if not self.visible:
            return

        # get the song data
        self.artist = entry.get_string(RB.RhythmDBPropType.ARTIST)
        self.title = entry.get_string(RB.RhythmDBPropType.TITLE)
        self.filepath = entry.get_string(RB.RhythmDBPropType.LOCATION)

        print("search lyrics for " + self.artist + " - " + self.title + ": " + self.filepath)

        self.was_corrected = False

        (self.clean_artist, self.clean_title, self.filepath) = self.clean_song_data(self.artist, self.title, self.filepath)
        self.path = self.build_cache_path(self.clean_artist, self.clean_title)

        self.scan_all_sources(self.clean_artist, self.clean_title, self.filepath, True)

    def clean_song_data(self, artist, title, filepath):
        # convert to lowercase
        artist = artist.lower()
        title = title.lower()

        # remove accents
        artist = unicodedata.normalize('NFKD', artist)
        artist = "".join([c for c in artist if not unicodedata.combining(c)])
        title = unicodedata.normalize('NFKD', title)
        title = "".join([c for c in title if not unicodedata.combining(c)])

        if self.ignore_brackets:
            LYRICS_TITLE_STRIP.append("\(.*\)")

        # replace ampersands and the like
        for exp in LYRICS_ARTIST_REPLACE:
            artist = re.sub(exp[0], exp[1], artist)
        for exp in LYRICS_TITLE_REPLACE:
            title = re.sub(exp[0], exp[1], title)

        # strip things like "(live at Somewhere)", "(acoustic)", etc
        for exp in LYRICS_TITLE_STRIP:
            title = re.sub(exp, '', title)

        # compress spaces
        title = title.strip()
        artist = artist.strip()
        
        if filepath is not "" or filepath is not None:
            filepath = Gio.File.new_for_uri(filepath).get_path()
        else:
            filepath = ""

        return (artist, title, filepath)

    def build_cache_path(self, artist, title):
        artist_folder = os.path.join(self.lyrics_folder, artist[:128])
        if not os.path.exists(artist_folder):
            os.mkdir(artist_folder)

        return os.path.join(artist_folder, title[:128] + '.lyric')

    def scan_selected_source_callback(self, action, activated_action):
        if not action.get_active():
            return

        source = activated_action
        if source == "SelectNothing" or source == self.current_source:
            return

        self.scan_source(source, self.clean_artist, self.clean_title)

    def scan_next_action_callback(self, action):
        if self.current_source is None or self.current_source == "From cache file":
            index = 0
        else:
            index = self.sources.index(self.current_source) + 1
            index = index % (len(self.sources) + 1)

        if index >= len(self.sources):
            source = "From cache file"
        else:
            source = self.sources[index]

        self.scan_source(source, self.clean_artist, self.clean_title)

    def scan_all_action_callback(self, action):
        self.scan_all_sources(self.clean_artist, self.clean_title, self.filepath, False)

    def search_online_action_callback(self, action):
        webbrowser.open("http://www.google.com/search?q=%s+%s+lyrics" % (self.clean_artist, self.clean_title))

    def instrumental_action_callback(self, action):
        lyrics = "-- Instrumental --"
        self.write_lyrics_to_cache(self.path, lyrics)
        self.current_source = None
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.show_lyrics, lyrics)

    def save_to_cache_action_callback(self, action):
        start, end = self.textbuffer.get_bounds()
        start.forward_lines(1)
        lyrics = self.textbuffer.get_text(start, end, False)

        self.write_lyrics_to_cache(self.path, lyrics)

    def clear_action_callback(self, action):
        self.textbuffer.set_text("")
        try:
            os.remove(self.path)
        except:
            print("No cache file found to clear")
        print("cleared lyrics")

    def edit_action_callback(self, action):
        # Unset event flag to indicate editing and so block all other threads which 
        # want to display new lyrics until editing is finished.
        self.edit_event.clear()

        # Conserve lyrics in order to restore original lyrics when editing is canceled 
        start, end = self.textbuffer.get_bounds()
        self.lyrics_before_edit = self.textbuffer.get_text(start, end, False)
        # remove sync tag
        self.textbuffer.remove_tag(self.sync_tag, start, end)
        # Conserve cache path in order to be able to correctly save edited lyrics although
        # the playing song might have changed during editing.
        self.path_before_edit = self.path

        self.set_menu_sensitive(False)

        # Enable editing and set cursor
        self.textview.set_cursor_visible(True)
        self.textview.set_editable(True)
        cursor = self.textbuffer.get_iter_at_line(1)
        self.textbuffer.place_cursor(cursor)
        self.textview.grab_focus()

        self.hbox.show()

    def preferences_dialog_action_callback(self, action):
        content = ConfigDialog().do_create_configure_widget()

        dialog = Gtk.Dialog(_('lLyrics Preferences'), self.shell.get_property('window'),
                            Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                            (Gtk.STOCK_OK, Gtk.ResponseType.OK))

        content_area = dialog.get_content_area()
        content_area.pack_start(content, True, True, 0)

        dialog.show_all()

        dialog.run()

        dialog.hide()

    def context_action_callback(self, action, param=None, data=None):
        page = self.shell.props.selected_page
        if not hasattr(page, "get_entry_view"):
            return

        selected = page.get_entry_view().get_selected_entries()
        if not selected:
            print("nothing selected")
            return

        # if multiple selections, take first
        entry = selected[0]

        self.showing_on_demand = True
        self.back_button.show()

        # Disconnect from song-changed and elapsed-change signals
        if self.psc_id:
            self.player.disconnect(self.psc_id)
            self.player.disconnect(self.pec_id)
            self.psc_id = None
            self.pec_id = None

        if not self.visible:
            self.toggle_action_group.get_action("ToggleLyricSideBar").set_active(True)

        self.search_lyrics(self.player, entry)

    def save_button_callback(self, button):
        self.textview.set_cursor_visible(False)
        self.textview.set_editable(False)
        self.hbox.hide()

        # get lyrics without artist-title header
        start, end = self.textbuffer.get_bounds()
        start.forward_lines(1)
        lyrics = self.textbuffer.get_text(start, end, False)

        # save edited lyrics to cache file and audio tag
        self.write_lyrics_to_cache(self.path_before_edit, lyrics)

        # If playing song changed, set "searching lyrics..." (might be overwritten
        # immediately, if thread for the new song already found lyrics)
        if self.path != self.path_before_edit:
            self.textbuffer.set_text(_("searching lyrics..."))

        self.set_menu_sensitive(True)

        # Set event flag to indicate end of editing and wake all threads 
        # waiting to display new lyrics.
        self.edit_event.set()

    def cancel_button_callback(self, button):
        self.textview.set_cursor_visible(False)
        self.textview.set_editable(False)
        self.hbox.hide()

        # Restore original lyrics if playing song didn't change,
        # otherwise set "searching lyrics..." (might be overwritten
        # immediately, if thread for the new song already found lyrics)
        if self.path == self.path_before_edit:
            self.textbuffer.set_text(self.lyrics_before_edit)
            start = self.textbuffer.get_start_iter()
            end = start.copy()
            end.forward_to_line_end()
            self.textbuffer.apply_tag(self.tag, start, end)
        else:
            self.textbuffer.set_text(_("searching lyrics..."))

        self.set_menu_sensitive(True)

        # Set event flag to indicate end of editing and wake all threads 
        # waiting to display new lyrics.
        self.edit_event.set()

    def back_button_callback(self, button):
        # reconnect to signals
        self.psc_id = self.player.connect('playing-song-changed', self.search_lyrics)
        self.pec_id = self.player.connect('elapsed-changed', self.elapsed_changed)

        self.back_button.hide()
        self.showing_on_demand = False

        playing_entry = self.player.get_playing_entry()

        # if nothing is playing, clear lyrics and return
        if not playing_entry:
            self.textbuffer.set_text("")
            return

        # otherwise search lyrics
        self.search_lyrics(self.player, playing_entry)

    def set_displayed_text(self, text):
        self.textbuffer.set_text(text)

    def scan_source(self, source, artist, title):
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.set_displayed_text, _("searching lyrics..."))

        newthread = Thread(target=self._scan_source_thread, args=(source, artist, title))
        newthread.start()

    def _scan_source_thread(self, source, artist, title):
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.set_menu_sensitive, False)

        if source == "From cache file":
            lyrics = self.get_lyrics_from_cache(self.path)
        else:
            lyrics = self.get_lyrics_from_source(source, artist, title)

        # check if playing song changed
        if artist != self.clean_artist or title != self.clean_title:
            print("song changed")
            return

        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.set_menu_sensitive, True)

        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.show_lyrics, lyrics)

    def scan_all_sources(self, artist, title, filepath, cache):
        if self.edit_event.is_set():
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.set_displayed_text, _("searching lyrics..."))

        newthread = Thread(target=self._scan_all_sources_thread, args=(artist, title, filepath, cache))
        newthread.start()

    def _scan_all_sources_thread(self, artist, title, filepath, cache):
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.set_menu_sensitive, False)

        lyrics = ""
        if lyrics == "":
            lyrics = self.get_lyrics_from_tags(filepath)

        if cache and lyrics == "":
            lyrics = self.get_lyrics_from_cache(self.path)

        if lyrics == "":
            i = 0
            while lyrics == "" and i in range(len(self.sources)):
                lyrics = self.get_lyrics_from_source(self.sources[i], artist, title)
                # check if playing song changed
                if artist != self.clean_artist or title != self.clean_title:
                    print("song changed")
                    return
                i += 1

        # We can't display new lyrics while user is editing! 
        self.edit_event.wait()

        # check if playing song changed
        if artist != self.clean_artist or title != self.clean_title:
            print("song changed")
            return

        if lyrics == "":
            # check for lastfm corrections
            if not self.was_corrected:
                self.was_corrected = True
                (artist, title, corrected) = Util.get_lastfm_correction(artist, title)
                if corrected:
                    (self.clean_artist, self.clean_title, self.clean_filepath) = self.clean_song_data(artist, title, filepath)
                    self._scan_all_sources_thread(self.clean_artist, self.clean_title, self.clean_filepath, False)
                    return

            self.current_source = None

        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.set_menu_sensitive, True)

        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.show_lyrics, lyrics)

    def get_lyrics_from_cache(self, path):
        self.current_source = "From cache file"

        # try to load lyrics from cache
        if os.path.exists(path):
            try:
                cachefile = open(path, "r")
                lyrics = cachefile.read()
                cachefile.close()
            except:
                print("error reading cache file")
                return ""

            print("got lyrics from cache")
            return lyrics

        return ""

    def get_lyrics_from_tags(self, filepath):
        tags_dict = TinyTag.get(filepath).as_dict()
        possible_keys = ['lyrics', 'unsynched lyrics']
        lyrics = ""
        for k in possible_keys:
            if k in tags_dict.keys():
                lyrics_list = tags_dict[k]
                lyrics = "\n".join(lyrics_list).strip()
                break

        return lyrics

    def write_lyrics_to_cache(self, path, lyrics):
        try:
            cachefile = open(path, "w+")
            cachefile.write(lyrics)
            cachefile.close()
            print("wrote lyrics to cache file")
        except:
            print("error writing lyrics to cache file")

    def get_lyrics_from_source(self, source, artist, title):
        # Playing song might change during search, so we want to 
        # conserve the correct cache path.
        path = self.path

        print("source: " + source)
        self.current_source = source

        parser = self.dict[source].Parser(artist, title)
        try:
            lyrics = parser.parse()
        except Exception as e:
            print("Error in parser " + source)
            print(str(e))
            return ""

        if lyrics != "":
            print("got lyrics from source")
            lyrics = "%s\n\n(lyrics from %s)" % (lyrics, source)

            if self.cache:
                self.write_lyrics_to_cache(path, lyrics)

        return lyrics

    def show_lyrics(self, lyrics):
        if self.current_source is None:
            self.set_radio_menu_item_active("SelectNothing")
        elif self.current_source == "From cache file":
            self.set_radio_menu_item_active(_("From cache file"))
        else:
            self.set_radio_menu_item_active(self.current_source)

        if lyrics == "":
            print("no lyrics found")
            lyrics = _("No lyrics found")
        else:
            lyrics, self.tags = Util.parse_lrc(lyrics)

        self.textbuffer.set_text("%s - %s\n%s" % (self.artist, self.title, lyrics))

        # make 'artist - title' header bold and underlined 
        start = self.textbuffer.get_start_iter()
        end = start.copy()
        end.forward_to_line_end()
        self.textbuffer.apply_tag(self.tag, start, end)

    def elapsed_changed(self, player, seconds):
        if not self.tags or not self.edit_event.is_set():
            return

        matching_tag = None
        for tag in self.tags:
            time, _ = tag
            if time > seconds:
                break
            matching_tag = tag

        if matching_tag is None or self.current_tag == matching_tag:
            return

        self.current_tag = matching_tag

        # remove old tag
        start, end = self.textbuffer.get_bounds()
        self.textbuffer.remove_tag(self.sync_tag, start, end)

        # highlight next line
        line = self.tags.index(self.current_tag) + 1
        start = self.textbuffer.get_iter_at_line(line)
        end = start.copy()
        end.forward_to_line_end()
        self.textbuffer.apply_tag(self.sync_tag, start, end)
        self.textview.scroll_to_iter(start, 0.1, False, 0, 0)

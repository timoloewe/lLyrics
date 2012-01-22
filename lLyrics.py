from gi.repository import GObject, Peas, Gdk
from gi.repository import RB
from gi.repository import Gtk
from threading import Thread
import chartlyricsParser

llyrics_ui = """
<ui>
    <menubar name="MenuBar">
        <menu name="ViewMenu" action="View">
            <menuitem name="Lyrics" action="ToggleLyricSideBar" />
        </menu>
    </menubar>
</ui>
"""


class lLyrics(GObject.GObject, Peas.Activatable):
    __gtype_name = 'lLyrics'
    object = GObject.property(type=GObject.GObject)
    
    def __init__(self):
        GObject.GObject.__init__(self)
        GObject.threads_init()
        Gdk.threads_init()

    def do_activate(self):
        """
        activate plugin
        """        
        self.shell = self.object
        self.init_sidebar()

        self.player = self.shell.props.shell_player
        # search lyrics if already playing (this will be the case if user reactivates plugin during playback)
        if self.player.props.playing:
                self.search_lyrics(self.player, self.player.get_playing_entry())
        # search lyrics if song changes 
        self.psc_id = self.player.connect ('playing-song-changed', self.search_lyrics)
        
        # Add button to toggle visibility of pane
        self.action = ('ToggleLyricSideBar','gtk-info', _("Lyrics Sidebar"),
                        None, _("Change the visibility of the lyrics sidebar"),
                        self.toggle_visibility, True)
        self.action_group = Gtk.ActionGroup(name='lLyricsPluginActions')
        self.action_group.add_toggle_actions([self.action])
        uim = self.shell.props.ui_manager
        uim.insert_action_group (self.action_group, 0)
        self.ui_id = uim.add_ui_from_string(llyrics_ui)
        uim.ensure_update()
        
        print "activated plugin lLyrics"

    def do_deactivate(self):
        """
        deactivate plugin
        """        
        if self.visible:
            self.shell.remove_widget (self.vbox, RB.ShellUILocation.RIGHT_SIDEBAR)
        self.vbox = None
        self.textview = None
        self.textbuffer = None
        self.player.disconnect(self.psc_id)
        del self.psc_id
        self.player_cb_ids = None
        self.visible = None
        self.player = None
        uim = self.shell.props.ui_manager
        uim.remove_ui (self.ui_id)
        uim.remove_action_group (self.action_group)
        self.action = None
        self.action_group = None
        
        self.shell = None

        print "deactivated plugin lLyrics"
       
    def init_sidebar(self):
        self.vbox = Gtk.VBox()
        frame = Gtk.Frame()
        label = Gtk.Label(_("Lyrics"))
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.set_label_align(0.0,0.0)
        frame.set_label_widget(label)
        label.set_use_markup(True)
        label.set_padding(0,4)
        
        # create a TextView for displaying lyrics
        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_left_margin(10)
        self.textview.set_right_margin(10)
        self.textview.set_pixels_above_lines(10)
        self.textview.set_pixels_below_lines(10)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        
        # create a ScrollView
        sw = Gtk.ScrolledWindow()
        sw.add(self.textview)
        
        # initialize a TextBuffer to store lyrics in
        self.textbuffer = Gtk.TextBuffer()
        self.textview.set_buffer(self.textbuffer)


        # pack everything into side pane
        self.vbox.pack_start  (frame, False, True, 0)
        self.vbox.pack_start (sw, True, True, 0)

        self.vbox.show_all()
        self.vbox.set_size_request(200, -1)
        self.shell.add_widget (self.vbox, RB.ShellUILocation.RIGHT_SIDEBAR, True, True)
        self.visible = True 
    
    def toggle_visibility (self, action):
        if not self.visible:
            self.shell.add_widget (self.vbox, RB.ShellUILocation.RIGHT_SIDEBAR, True, True)
            self.visible = True
        else:
            self.shell.remove_widget (self.vbox, RB.ShellUILocation.RIGHT_SIDEBAR)
            self.visible = False
        
    def search_lyrics(self, player, entry):
        self.textbuffer.set_text("searching lyrics...")
        newthread = Thread(target=self._search_lyrics_thread, args=(player, entry))
        newthread.start()
    
    def _search_lyrics_thread(self, player, entry):
        if entry is not None:
            artist = entry.get_string(RB.RhythmDBPropType.ARTIST)
            title = entry.get_string(RB.RhythmDBPropType.TITLE)
        else:
            artist = "none"
            title = "none"     
        
        parser = chartlyricsParser.chartlyricsParser(artist, title)
        lyrics = parser.parse()
        Gdk.threads_enter()
        self.textbuffer.set_text(artist + " - " + title + "\n" + lyrics)
        Gdk.threads_leave()

    
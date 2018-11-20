#!/usr/bin/python

import sys
import re
import threading
import argparse
import signal

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

from blessed import Terminal


dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)


class MetaWriter:
    filename             = ""
    output_format        = ""
    output_format_artist = ""
    output_format_title  = ""
    output_format_album  = ""
    last_output          = ""

    def __init__(self, filename, output_format, output_format_artist, output_format_title, output_format_album):
        self.filename             = filename
        self.output_format        = output_format
        self.output_format_album  = output_format_album
        self.output_format_artist = output_format_artist
        self.output_format_title  = output_format_title

    def write_meta(self, metadata):
        artist = metadata["xesam:artist"][0] if "xesam:artist" in metadata else ""
        title  = metadata["xesam:title"]     if "xesam:title"  in metadata else ""
        album  = metadata["xesam:album"]     if "xesam:album"  in metadata else ""

        self.write(self.output_format.format(
            artist = self.output_format_artist.format(artist) if artist != "" else "",
            title  = self.output_format_title.format(title)   if title  != "" else "",
            album  = self.output_format_album.format(album)   if album  != "" else ""
        ))

    def write(self, text):
        self.last_output = text
        f = open(self.filename, "w")
        f.write(text)
        f.close()


class PlayerSelector(threading.Thread):
    service_regex   = re.compile("^org\.mpris\.MediaPlayer2\.")
    bus             = None
    loop            = None
    signal_receiver = None
    players         = {}
    players_indexes = []
    active_player   = ""
    menu            = None
    meta_writer     = None

    def __init__(self, meta_writer, menu=None):
        super().__init__()

        self.bus         = dbus.SessionBus()
        self.meta_writer = meta_writer
        self.menu        = menu

    def set_menu(self, menu):
        self.menu = menu
    
    def get_players(self):
        self.players         = {}
        self.players_indexes = []

        bus_path  = self.bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
        bus_proxy = dbus.Interface(bus_path, "org.freedesktop.DBus")

        for service in bus_proxy.ListNames():
            if self.service_regex.match(service):
                split_service  = service.split(".")
                name_start_index = len(split_service[0]) + len(split_service[1]) + len(split_service[2]) + 3

                player_path  = self.bus.get_object(service, "/org/mpris/MediaPlayer2")
                player_props = dbus.Interface(player_path, "org.freedesktop.DBus.Properties")
                player_name  = service[name_start_index:]
                try:
                    player_name = player_props.Get("org.mpris.MediaPlayer2.Player", "Identity")
                except dbus.exceptions.DBusException:
                    pass

                self.players[service] = player_name
                self.players_indexes.append(service)

        self.set_active_player(self.active_player)

    def set_active_player_index(self, player_index):
        if player_index < len(self.players_indexes):
            self.set_active_player(self.players_indexes[player_index])

    def set_active_player(self, player_id):
        if player_id in self.players:
            self.active_player = player_id
        elif len(self.players) != 0:
            self.active_player = next(iter(self.players.keys()))
        else:
            self.active_player = ""

        if self.signal_receiver is not None:
            self.signal_receiver.remove()

        if self.active_player != "":
            self.signal_receiver = self.bus.add_signal_receiver(
                handler_function = self.playing_song_changed,
                bus_name         = self.active_player,
                dbus_interface   = "org.freedesktop.DBus.Properties",
                signal_name      = "PropertiesChanged",
                path             = "/org/mpris/MediaPlayer2"
            )

            player_path  = self.bus.get_object(self.active_player, "/org/mpris/MediaPlayer2")
            player_props = dbus.Interface(player_path, "org.freedesktop.DBus.Properties")

            self.meta_writer.write_meta(player_props.Get("org.mpris.MediaPlayer2.Player", "Metadata"))
        else:
            self.meta_writer.write("")


    def run(self):
        bus_path  = self.bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
        bus_proxy = dbus.Interface(bus_path, "org.freedesktop.DBus")
        bus_proxy.connect_to_signal("NameOwnerChanged", self.dbus_name_owner_changed)

        self.get_players()
        self.menu.refresh()

        self.loop = GLib.MainLoop()
        self.loop.run()
            
    def quit(self):
        self.loop.quit()
            
    def dbus_name_owner_changed(self, name, old_owner, new_owner):
        if self.service_regex.match(name):
            self.get_players()
            self.menu.refresh()

    def playing_song_changed(self, player, changed_properties, invalidated_properties):
        if "Metadata" in changed_properties:
            self.meta_writer.write_meta(changed_properties["Metadata"])
            self.menu.refresh()


class Menu(threading.Thread):
    refresh_cond    = threading.Condition()
    refresh_flag    = True
    exit_flag       = False
    player_selector = None
    meta_writer     = None

    def __init__(self, term, player_selector, meta_writer):
        super().__init__()

        self.player_selector = player_selector
        self.meta_writer     = meta_writer

    def refresh(self, exit_flag=False):
        with self.refresh_cond:
            self.refresh_flag = True
            self.exit_flag    = self.exit_flag or exit_flag
            self.refresh_cond.notify()

    def run(self):
        while not self.exit_flag:
            with self.refresh_cond:
                while not self.refresh_flag and not self.exit_flag:
                    self.refresh_cond.wait()

                self.refresh_flag = False

                with term.fullscreen():
                    print(term.move(0, 0) + term.bold_bright_white_on_bright_black(("{0:<{width}}").format("MPRIS To Text", width=term.width)) + "\n")
                    print(term.move_x(2) + term.bold("Player: ") + term.move_up())
                    
                    for i, (id, name) in enumerate(self.player_selector.players.items()):
                        output = "%d: %s" % (i, name)

                        if id == self.player_selector.active_player:
                            print(term.move_x(10) + term.standout(output))
                        else:
                            print(term.move_x(10) + output)

                    print(term.move_x(2) + term.bold("File:   ") + self.meta_writer.filename)
                    print(term.move_x(2) + term.bold("Output: ") + "\n".join(term.wrap(self.meta_writer.last_output, width=term.width - 10, subsequent_indent=" " * 10)))

                    print(term.move_x(0) + "\nEnter number to select player or q to exit." + term.move_up())

        with term.fullscreen():
            print(term.move(0, 0) + "Exiting...")


class Input(threading.Thread):
    player_selector = None
    meta_writer     = None
    menu            = None

    def __init__(self, term, player_selector, meta_writer, menu):
        super().__init__()

        self.player_selector = player_selector
        self.meta_writer     = meta_writer
        self.menu            = menu

    def run(self):
        with term.cbreak():
            val = None

            while val not in (u'q', u'Q',):
                val = term.inkey(timeout=5)

                if not val or val.is_sequence or not val.isnumeric():
                    continue

                if int(val) < len(self.player_selector.players):
                    self.player_selector.set_active_player_index(int(val))
                    self.menu.refresh()

            self.menu.refresh(True)
            self.player_selector.quit()


def on_resize(*args):
    global menu_thread

    menu_thread.refresh()


def create_writer():
    parser = argparse.ArgumentParser(description="Write metadata from MPRIS-compliant media players into a text file.")
    parser.add_argument("--file",
        type    = str,
        dest    = "filename",
        default = "/tmp/mpris_info.txt",
        help    = "Full path to file (default: \"/tmp/mpris_info.txt\")"
    )
    parser.add_argument("--format-artist",
        type    = str,
        dest    = "format_artist",
        default = "{}    ",
        help    = "Format string for the artist part (default: \"{}    \")"
    )
    parser.add_argument("--format-title",
        type    = str,
        dest    = "format_title",
        default = "\"{}\"",
        help    = "Format string for the title part (default: \"\"{}\"\")"
    )
    parser.add_argument("--format-album",
        type    = str,
        dest    = "format_album",
        default = "  from  \"{}\"",
        help    = "Format string for the album part (default: \"  from  \"{}\"\")"
    )
    parser.add_argument("--format",
        type    = str,
        dest    = "format",
        default = "{artist}{title}{album}            ",
        help    = "Full format string (default: \"{artist}{title}{album}            \")"
    )

    args = parser.parse_args()

    return MetaWriter(
        filename             = args.filename,
        output_format        = args.format,
        output_format_artist = args.format_artist,
        output_format_title  = args.format_title,
        output_format_album  = args.format_album
    )


term        = Terminal()
meta_writer = create_writer()

signal.signal(signal.SIGWINCH, on_resize)

player_selector = PlayerSelector(meta_writer)
menu_thread = Menu(term, player_selector, meta_writer)
player_selector.set_menu(menu_thread)
input_thread = Input(term, player_selector, meta_writer, menu_thread)

player_selector.start()
menu_thread.start()
input_thread.start()

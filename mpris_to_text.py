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


name_regex   = re.compile("^org\.mpris\.MediaPlayer2\.")
bus          = dbus.SessionBus()
term         = Terminal()

refresh_cond = threading.Condition()
refresh_flag = True
exit_flag    = False

players                              = []
player_id_to_index                   = {}
active_player                        = ""
current_output                       = ""
playing_song_changed_signal_receiver = None

filename           = ""
meta_format        = ""
meta_format_artist = ""
meta_format_title  = ""
meta_format_album  = ""


def track_string(metadata):
    artist = metadata["xesam:artist"][0] if "xesam:artist" in metadata else ""
    title  = metadata["xesam:title"]     if "xesam:title"  in metadata else ""
    album  = metadata["xesam:album"]     if "xesam:album"  in metadata else ""

    return meta_format.format(
        artist = meta_format_artist.format(artist) if artist != "" else "",
        title  = meta_format_title.format(title)   if title  != "" else "",
        album  = meta_format_album.format(album)   if album  != "" else ""
    )


def write_track(track):
    global filename
    global current_output
    
    current_output = track
    f = open(filename, "w")
    f.write(track)
    f.close()


def playing_song_changed(player, changed_properties, invalidated_properties):
    global refresh_cond
    global refresh_flag

    if "Metadata" in changed_properties:
        write_track(track_string(changed_properties["Metadata"]))
        with refresh_cond:
            refresh_flag = True
            refresh_cond.notify()


def dbus_name_owner_changed(name, old_owner, new_owner):
    global refresh_cond
    global refresh_flag

    if name_regex.match(name):
        get_players()
        with refresh_cond:
            refresh_flag = True
            refresh_cond.notify()


def set_active_player(player_id):
    global bus
    global players
    global player_id_to_index
    global active_player
    global playing_song_changed_signal_receiver

    if player_id in player_id_to_index:
        active_player = player_id
    elif len(player_id_to_index) != 0:
        active_player = next(iter(player_id_to_index))
    else:
        active_player = ""

    if playing_song_changed_signal_receiver is not None:
        playing_song_changed_signal_receiver.remove()

    if active_player != "":
        playing_song_changed_signal_receiver = bus.add_signal_receiver(
            handler_function = playing_song_changed,
            bus_name         = active_player,
            dbus_interface   = "org.freedesktop.DBus.Properties",
            signal_name      = "PropertiesChanged",
            path             = "/org/mpris/MediaPlayer2"
        )

        player_path  = bus.get_object(active_player, "/org/mpris/MediaPlayer2")
        player_props = dbus.Interface(player_path, "org.freedesktop.DBus.Properties")

        write_track(track_string(player_props.Get("org.mpris.MediaPlayer2.Player", "Metadata")))
    else:
        write_track("")


def get_players():
    global players
    global player_id_to_index
    global active_player

    players            = []
    player_id_to_index = {}
    bus_path           = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
    bus_proxy          = dbus.Interface(bus_path, "org.freedesktop.DBus")
    names              = bus_proxy.ListNames()

    for name in names:
        if name_regex.match(name):
            split_name     = name.split(".")
            id_start_index = len(split_name[0]) + len(split_name[1]) + len(split_name[2]) + 3

            player_path  = bus.get_object(name, "/org/mpris/MediaPlayer2")
            player_props = dbus.Interface(player_path, "org.freedesktop.DBus.Properties")
            player_id    = name[id_start_index:]
            try:
                player_id = player_props.Get("org.mpris.MediaPlayer2.Player", "Identity")
            except dbus.exceptions.DBusException:
                pass

            players.append((name, player_id))
            player_id_to_index[name] = len(players) - 1

    set_active_player(active_player)


def draw_menu():
    global term
    global players
    global refresh_cond
    global refresh_flag
    global exit_flag
    global current_output
    global filename

    while not exit_flag:
        with refresh_cond:
            while not refresh_flag and not exit_flag:
                refresh_cond.wait()

            refresh_flag = False

            with term.fullscreen():
                print(term.move(0, 0) + term.bold_bright_white_on_bright_black(("{0:<{width}}").format("MPRIS To Text", width=term.width)) + "\n")
                print(term.move_x(2) + term.bold("Player: ") + term.move_up())
                
                for i in range(len(players)):
                    player = players[i]
                    output = "%d: %s" % (i, player[1])

                    if players[player_id_to_index[active_player]][0] == player[0]:
                        print(term.move_x(10) + term.standout(output))
                    else:
                        print(term.move_x(10) + output)

                print(term.move_x(2) + term.bold("File:   ") + filename)
                print(term.move_x(2) + term.bold("Output: ") + "\n".join(term.wrap(current_output, width=term.width - 10, subsequent_indent=" " * 10)))

                print(term.move_x(0) + "\nEnter number to select player or q to exit." + term.move_up())

    with term.fullscreen():
        print(term.move(0, 0) + "Exiting...")


def on_resize(*args):
    global refresh_cond
    global refresh_flag

    with refresh_cond:
        refresh_flag = True
        refresh_cond.notify()


def init_dbus():
    global bus

    bus_path  = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
    bus_proxy = dbus.Interface(bus_path, "org.freedesktop.DBus")

    bus_proxy.connect_to_signal("NameOwnerChanged", dbus_name_owner_changed)

    get_players()


def init_blessed():
    global bus
    global term
    global refresh_cond
    global refresh_flag
    global exit_flag
    global loop

    with term.cbreak():
        val = None

        while val not in (u'q', u'Q',):
            val = term.inkey(timeout=5)

            if not val or val.is_sequence or not val.isnumeric():
                continue

            if int(val) < len(players):
                set_active_player(players[int(val)][0])
                with refresh_cond:
                    refresh_flag = True
                    refresh_cond.notify()

        with refresh_cond:
            exit_flag = True
            refresh_cond.notify()

        loop.quit()


def read_args():
    global filename
    global meta_format
    global meta_format_artist
    global meta_format_title
    global meta_format_album

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

    args               = parser.parse_args()
    filename           = args.filename
    meta_format        = args.format
    meta_format_artist = args.format_artist
    meta_format_title  = args.format_title
    meta_format_album  = args.format_album



read_args()

signal.signal(signal.SIGWINCH, on_resize)

init_dbus()

blessed_thread = threading.Thread(target=init_blessed)
blessed_thread.start()

menu_thread = threading.Thread(target=draw_menu)
menu_thread.start()

loop = GLib.MainLoop()
loop.run()

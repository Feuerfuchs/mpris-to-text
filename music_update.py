#!/usr/bin/python

import sys
import asyncio
import re
import threading

from blessed import Terminal

import dbussy as dbus
import ravel


loop       = asyncio.get_event_loop()
name_regex = re.compile("^org\.mpris\.MediaPlayer2\.")

bus          = None
term         = Terminal()
refresh_cond = threading.Condition()
refresh_flag = True
exit_flag    = False
exit_task    = asyncio.Future()

players            = []
player_id_to_index = {}
active_player      = ""
current_output     = ""


def track_string(metadata):
    artist = metadata["xesam:artist"][1][0] if "xesam:artist" in metadata else ""
    title  = metadata["xesam:title"][1]     if "xesam:title"  in metadata else ""
    album  = metadata["xesam:album"][1]     if "xesam:album"  in metadata else ""

    track = ""
    if artist != "": track = artist + "    "
    track = track + "\"" + title + "\""
    if album != "": track = track + "  from  \"" + album + "\""

    return track + "            "


def write_track(track):
    global current_output
    
    current_output = track
    f = open("/mnt/Data/stream_info.txt", "w")
    f.write(track)
    f.close()


@ravel.signal(name = "PropertiesChanged", in_signature = "sa{sv}as", args_keyword = "args")
def playing_song_changed(args):
    global refresh_cond
    global refresh_flag

    [ player, changed_properties, invalidated_properties ] = args
    if "Metadata" in changed_properties:
        write_track(track_string(changed_properties["Metadata"][1]))
        with refresh_cond:
            refresh_flag = True
            refresh_cond.notify()


@ravel.signal(name = "NameOwnerChanged", in_signature = "sss", args_keyword = "args")
def dbus_name_owner_changed(args):
    global refresh_cond
    global refresh_flag

    [ name, old_owner, new_owner ] = args
    if name_regex.match(name):
        get_players()
        with refresh_cond:
            refresh_flag = True
            refresh_cond.notify()


def set_active_player(player_id):
    global bus
    global active_player

    if player_id in player_id_to_index:
        active_player = player_id
    elif len(player_id_to_index) != 0:
        active_player = next(iter(player_id_to_index))
    else:
        active_player = ""

    for (name, player_id) in players:
        bus.unlisten_propchanged(
            path      = "/org/mpris/MediaPlayer2",
            interface = name,
            func      = playing_song_changed,
            fallback  = True
        )

    if active_player != "":
        bus.listen_propchanged(
            path      = "/org/mpris/MediaPlayer2",
            interface = active_player,
            func      = playing_song_changed,
            fallback  = True
        )

        player_path  = bus[active_player]["/org/mpris/MediaPlayer2"]
        player_props = player_path.get_interface("org.freedesktop.DBus.Properties")

        write_track(track_string(player_props.Get("org.mpris.MediaPlayer2.Player", "Metadata")[0][1]))
    else:
        write_track("")


def get_players():
    global players
    global player_id_to_index

    players            = []
    player_id_to_index = {}
    bus_proxy          = bus["org.freedesktop.DBus"]["/org/freedesktop/DBus"].get_interface("org.freedesktop.DBus")
    names              = bus_proxy.ListNames()

    for name in names[0]:
        if name_regex.match(name):
            split_name     = name.split(".")
            id_start_index = len(split_name[0]) + len(split_name[1]) + len(split_name[2]) + 3

            player_path  = bus[name]["/org/mpris/MediaPlayer2"]
            player_proxy = player_path.get_interface("org.mpris.MediaPlayer2")
            player_id    = name[id_start_index:]
            try:
                player_id = player_proxy.Identity
            except AttributeError:
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

    while not exit_flag:
        with refresh_cond:
            while not refresh_flag and not exit_flag:
                refresh_cond.wait()

            refresh_flag = False

            with term.fullscreen():
                print(term.move(0, 0) + term.bold("MPRIS to Text") + "\n")
                
                for i in range(len(players)):
                    player = players[i]
                    output = "%d: %s" % (i, player[1])

                    if players[player_id_to_index[active_player]][0] == player[0]:
                        print(term.move_x(8) + term.standout_bold(output))
                    else:
                        print(term.move_x(8) + output)

                print(term.move_x(0) + "\n" + term.bold("Current output:") + " " + current_output)

                print(term.move_x(0) + "\n\nEnter number to select player or q to exit." + term.move_up())

    with term.fullscreen():
        print(term.move(0, 0) + "Exiting...")


def init_dbus():
    global bus
    global loop

    bus = ravel.session_bus()
    bus.attach_asyncio(loop)
    bus.listen_signal(
        path      = "/org/freedesktop/DBus",
        interface = "org.freedesktop.DBus",
        name      = "NameOwnerChanged",
        func      = dbus_name_owner_changed,
        fallback  = True
    )

    get_players()


def init_blessed():
    global bus
    global term
    global refresh_cond
    global refresh_flag
    global exit_flag

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

        exit_task.set_result(True)


init_dbus()

blessed_thread = threading.Thread(target=init_blessed)
blessed_thread.start()

menu_thread = threading.Thread(target=draw_menu)
menu_thread.start()

loop.run_until_complete(exit_task)

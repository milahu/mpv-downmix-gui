#!/usr/bin/env python3

"""
mpv GUI to modify the parameters to downmix surround sound to stereo

a GUI video player that allows to
fine-tune the coefficients of the downmix formula
while the video keeps playing

mpv with interactive control of the audio filter option
mpv --af='pan="stereo|..."' video.mp4

aka: equalizer for surround audio channels

gui for
https://superuser.com/questions/852400/properly-downmix-5-1-to-stereo-using-ffmpeg

please remember to normalize the volume.
in most movie releases, audio tracks are too quiet

1. downmix to stereo: ffmpeg -af pan=...
2. analyze volume: ffmpeg -af loudnorm=i=-14:lra=7:tp=-2:offset=0:linear=true:print_format=json
3. downmix to stereo and normalize volume: ffmpeg -af pan=...,loudnorm=...

https://ffmpeg.org/ffmpeg-filters.html#loudnorm
"""

import sys
import os
import subprocess
import socket
import tempfile
import time
import logging
import atexit

import tkinter as tk
from tkinter import ttk

from .tk_scale_debounced import tk_scale_debounced

# https://github.com/iwalton3/python-mpv-jsonipc
from . import python_mpv_jsonipc

from . import downmix_rfc7845



def get_channel_volume(coefficients, channel):
    "channel volume: sum of left and right"
    L, R = coefficients["FL"][channel], coefficients["FR"][channel]
    return R + L

def get_channel_balance(coefficients, channel):
    "channel balance: difference R-L by sum R+L"
    L, R = coefficients["FL"][channel], coefficients["FR"][channel]
    return (R - L) / (R + L)

def get_left_right_coefficient(volume, balance):
    return (
        (((1 - balance) * volume) / 2),
        (((1 + balance) * volume) / 2),
    )



# see also
# mpv --audio-channels=help | grep 5.1
input_channel_names_by_layout = dict()

N = input_channel_names_by_layout

# empty            ()
N["empty"] = (
    (None, None, None),
    (None, None, None),
    (None, None, None),
)

# mono             (fc)
# 1.0              (fc)
N["mono"] = N["1.0"] = (
    (None, "FC", None),
    (None, None, None),
    (None, None, None),
)

# stereo           (fl-fr)
# 2.0              (fl-fr)
N["stereo"] = N["2.0"] = (
    ("FL", None, "FR"),
    (None, None, None),
    (None, None, None),
)

# 2.1              (fl-fr-lfe)
N["2.1"] = (
    ("FL", None, "FR"),
    (None, "LFE", None),
    (None, None, None),
)

# 3.0              (fl-fr-fc)
N["3.0"] = (
    ("FL", "FC", "FR"),
    (None, None, None),
    (None, None, None),
)

# 3.0(back)        (fl-fr-bc)
N["3.0(back)"] = (
    ("FL", None, "FR"),
    (None, None, None),
    (None, "BC", None),
)

# 3.1              (fl-fr-fc-lfe)
# left, center, right, LFE
N["3.1"] = (
    ("FL", "FC", "FR"),
    (None, "LFE", None),
    (None, None, None),
)

# 3.1(back)        (fl-fr-lfe-bc)
N["3.1(back)"] = (
    ("FL", None, "FR"),
    (None, "LFE", None),
    (None, "BC", None),
)

# 4.0              (fl-fr-fc-bc)
N["4.0"] = (
    ("FL", "FC", "FR"),
    (None, None, None),
    (None, "BC", None),
)

# 4.1              (fl-fr-fc-lfe-bc)
N["4.1"] = (
    ("FL", "FC", "FR"),
    (None, "LFE", None),
    (None, "BC", None),
)

# quad             (fl-fr-bl-br)
# Quadraphonic Channel Mapping: FL FR BL BR
# front left, front right, back left, back right
N["quad"] = (
    ("FL", None, "FR"),
    (None, None, None),
    ("BL", None, "BR"),
)

# 4.1(alsa)        (fl-fr-bl-br-lfe)
# quad + LFE
N["4.1(alsa)"] = (
    ("FL", None, "FR"),
    (None, "LFE", None),
    ("BL", None, "BR"),
)

# quad(side)       (fl-fr-sl-sr)
N["quad(side)"] = (
    ("FL", None, "FR"),
    ("SL", None, "SR"),
    (None, None, None),
)

# 5.0              (fl-fr-fc-bl-br)
# 5.0(alsa)        (fl-fr-bl-br-fc)
# 5.0 Surround Mapping: FL FC FR RL RR
# 5.1 without LFE
# front left, front center, front right, back left, back right
N["5.0"] = N["5.0(alsa)"] = (
    ("FL", "FC", "FR"),
    (None, None, None),
    ("BL", None, "BR"),
)

# 5.0(side)        (fl-fr-fc-sl-sr)
N["5.0(side)"] = (
    ("FL", "FC", "FR"),
    ("SL", None, "SR"),
    (None, None, None),
)

# 5.1              (fl-fr-fc-lfe-bl-br)
# 5.1(alsa)        (fl-fr-bl-br-fc-lfe)
# 5.1 Surround Mapping: FL FC FR RL RR LFE
# front left, front center, front right, back left, back right, LFE
# note: ffmpeg says "back" instead of "rear" -> "BL" instead of "RL"
N["5.1"] = N["5.1(alsa)"] = (
    ("FL", "FC", "FR"),
    (None, "LFE", None),
    ("BL", None, "BR"),
)

# 5.1(side)        (fl-fr-fc-lfe-sl-sr)
N["5.1(side)"] = (
    ("FL", "FC", "FR"),
    ("SL", "LFE", "SR"),
    (None, None, None),
)

# 6.0              (fl-fr-fc-bc-sl-sr)
N["6.0"] = (
    ("FL", "FC", "FR"),
    ("SL", None, "SR"),
    (None, "BC", None),
)

# 6.0(front)       (fl-fr-flc-frc-sl-sr)
# FIXME does not fit into 3x3 grid

# hexagonal        (fl-fr-fc-bl-br-bc)
N["hexagonal"] = (
    ("FL", "FC", "FR"),
    (None, None, None),
    ("BL", "BC", "BR"),
)

# 6.1              (fl-fr-fc-lfe-bc-sl-sr)
# 6.1 Surround Mapping: FL FC FR SL SR BC LFE
# front left, front center, front right, side left, side right, back center, LFE
# 5.1 + back center, "back" -> "side"
N["6.1"] = (
    ("FL", "FC", "FR"),
    ("SL", "LFE", "SR"),
    (None, "BC", None),
)

# 6.1(back)        (fl-fr-fc-lfe-bl-br-bc)
# hexagonal + LFE
N["6.1(back)"] = (
    ("FL", "FC", "FR"),
    (None, "LFE", None),
    ("BL", "BC", "BR"),
)

# 6.1(top)         (fl-fr-fc-lfe-bl-br-tc)
# FIXME does not fit into 3x3 grid

# 6.1(front)       (fl-fr-lfe-flc-frc-sl-sr)
# FIXME does not fit into 3x3 grid

# 7.0              (fl-fr-fc-bl-br-sl-sr)
N["7.0"] = (
    ("FL", "FC", "FR"),
    ("SL", None, "SR"),
    ("BL", None, "BR"),
)

# 7.0(front)       (fl-fr-fc-flc-frc-sl-sr)
# FIXME does not fit into 3x3 grid

# 7.0(rear)        (fl-fr-fc-bl-br-sdl-sdr)
# FIXME does not fit into 3x3 grid

# 7.1              (fl-fr-fc-lfe-bl-br-sl-sr)
# 7.1(alsa)        (fl-fr-bl-br-fc-lfe-sl-sr)
# 7.1 Surround Mapping: FL FC FR SL SR BL BR LFE
# front left, front center, front right, side left, side right, back left, back right, LFE
# 6.1 + BC -> BL BR
N["7.1"] = N["7.1(alsa)"] = (
    ("FL", "FC", "FR"),
    ("SL", "LFE", "SR"),
    ("BL", None, "BR"),
)

# 7.1(wide)        (fl-fr-fc-lfe-bl-br-flc-frc)
# FIXME does not fit into 3x3 grid

# 7.1(wide-side)   (fl-fr-fc-lfe-flc-frc-sl-sr)
# FIXME does not fit into 3x3 grid

# 7.1(top)         (fl-fr-fc-lfe-bl-br-tfl-tfr)
# FIXME does not fit into 3x3 grid

# 7.1(rear)        (fl-fr-fc-lfe-bl-br-sdl-sdr)
# FIXME does not fit into 3x3 grid

# 8.1 Surround Mapping: FL FC FR SL SR BL BC BR LFE
# front left, front center, front right, side left, side right, back left, back right, LFE
# 7.1 + BC
N["8.1"] = (
    ("FL", "FC", "FR"),
    ("SL", "LFE", "SR"),
    ("BL", "BC", "BR"),
)

# TODO more?



def main():

    logging.basicConfig(
        # DEBUG:mpv-jsonipc:command list: ...
        #level=logging.DEBUG,
    )

    if len(sys.argv) < 2:
        print("error: no arguments")
        print("usage:")
        print(f"  {sys.argv[0]} mpv_arg...")
        print("example:")
        print(f"  {sys.argv[0]} movie.mp4 --audio-file=audio.m4a")
        sys.exit(1)

    mpv_ipc_socket_path = tempfile.mktemp(prefix='mpv_ipc_socket.')

    mpv_args = [
        "mpv",
        f"--input-ipc-server={mpv_ipc_socket_path}",
    ] + sys.argv[1:]

    mpv_proc = subprocess.Popen(mpv_args)

    # wait for mpv to create the ipc socket
    time.sleep(1)
    mpv_ipc_client = None
    for _ in range(10):
        try:
            mpv_ipc_client = python_mpv_jsonipc.MPV(start_mpv=False, ipc_socket=mpv_ipc_socket_path)
        except FileNotFoundError:
            time.sleep(1)
    assert mpv_ipc_client, f"failed to open mpv ipc socket {mpv_ipc_socket_path}"

    #print("media_title", mpv_ipc_client.media_title)
    #print("time_pos", mpv_ipc_client.time_pos)
    # audio_params {'samplerate': 48000, 'channel-count': 6, 'channels': '5.1(side)', 'hr-channels': '5.1(side)', 'format': 'floatp'}
    # current_tracks None
    #for key in ["volume", "metadata", "audio_params"]:
    #    print(key, getattr(mpv_ipc_client, key))

    # state
    input_channel_layout = None
    downmix_coefficients = None
    input_channel_names = []
    scale_dict = dict()
    audio_filter = ""
    root_window = None
    show_root_window = True

    def after_change(key=None, value=None):
        nonlocal audio_filter
        for channel in downmix_coefficients["FL"]:
            volume = scale_dict[f"volume.{channel}"].get()
            balance = scale_dict[f"balance.{channel}"].get()
            c = downmix_coefficients
            c["FL"][channel], c["FR"][channel] = \
                get_left_right_coefficient(volume, balance)
        # TODO allow top copy audio filter from gui > options
        audio_filter = downmix_rfc7845.get_ffmpeg_audio_filter(downmix_coefficients)
        assert audio_filter.startswith("pan=stereo|FL=")
        # TODO also print gui settings: FL, FC, FR, LFE, ...
        print("\n" + audio_filter + "\n")
        # wrap the value in quotes
        af = 'pan="' + audio_filter[4:] + '"'
        mpv_ipc_client.af_cmd("set", af)

    def reset_downmix_to_rfc7845():
        nonlocal input_channel_layout, downmix_coefficients, scale_dict
        downmix_coefficients = downmix_rfc7845.get_coefficients(input_channel_layout)
        for channel in downmix_coefficients["FL"]:
            volume = get_channel_volume(downmix_coefficients, channel)
            balance = get_channel_balance(downmix_coefficients, channel)
            scale_dict[f"volume.{channel}"].set(volume)
            scale_dict[f"balance.{channel}"].set(balance)
        after_change()

    def change_audio_track(name, track):
        nonlocal input_channel_layout, downmix_coefficients, scale_dict, root_window, show_root_window
        # TODO? input_channel_names
        print("change_audio_track", name, track)
        if track == None:
            # no audio track
            print("audio track:", None)
            return
        id = track["id"]
        channel_layout = track.get("demux-channels") # "stereo", "5.1(side)", ...
        print("change_audio_track channel_layout", channel_layout)
        if channel_layout.startswith("unknown"):
            print(f"error: unknown channel layout {channel_layout}. set the channel layout with --audio-channels=layout, for example --audio-channels=3.1")
            num_channels = track.get("audio-channels")
            # TODO suggest possible layouts based on number of channels
            return

            mpv_ipc_client.quit()
            mpv_proc.kill()
            if os.path.exists(mpv_ipc_socket_path):
                os.unlink(mpv_ipc_socket_path)
            show_root_window = False
            if root_window:
                root_window.close()
            print(f"error: unknown channel layout {channel_layout}. set the channel layout with --audio-channels=layout, for example --audio-channels=3.1")
            num_channels = track.get("audio-channels")
            # TODO suggest possible layouts based on number of channels
            sys.exit(1)
        set_input_channel_layout(input_channel_layout)
        title = track.get("title")
        #num_channels = track["audio-channels"]
        bitrate = track.get("demux-bitrate", 0)
        codec = track.get("codec")
        print("audio track:", id, input_channel_layout, codec, bitrate/1000, title)
        # TODO update: input_channel_layout downmix_coefficients scale_dict
        reset_downmix_to_rfc7845()



    # root window
    root_window = tk.Tk()
    #root_window.geometry('300x200')
    #root_window.resizable(False, False)
    root_window.resizable(True, True)
    #root_window.title("downmix: " + mpv_ipc_client.media_title)
    root_window.title("downmix")

    notebook = ttk.Notebook(root_window)
    notebook.pack(pady=10, fill='both', expand=True)

    frame_dict = dict()

    for frame_id in ["volume", "balance", "options"]:
        frame = ttk.Frame(notebook)
        frame_dict[frame_id] = frame
        frame.pack(fill='both', expand=True)
        notebook.add(frame, text=frame_id)


    frame_id = "options"
    frame = frame_dict[frame_id]

    options_dict = dict()

    option_id = "lock sides"
    option = dict()
    options_dict[option_id] = option
    option["value"] = tk.BooleanVar()
    option["value"].set(1)
    option["checkbutton"] = tk.Checkbutton(frame, text=option_id, variable=option["value"])
    option["checkbutton"].pack()

    option_id = "reset to RFC 7845"
    option = dict()
    options_dict[option_id] = option
    option["button"] = tk.Button(frame, text=option_id, command=reset_downmix_to_rfc7845)
    option["button"].pack()

    def on_change(key, value):
        if options_dict["lock sides"]["value"].get() == 0:
            return
        this_side = key[-1]
        if not this_side in ("L", "R"):
            return
        other_value = value
        if key.startswith("balance."):
            other_value = -1 * other_value
        other_side = "L" if this_side == "R" else "R"
        other_key = key[:-1] + other_side
        other_scale = scale_dict[other_key]
        other_scale.set(other_value)
        #print(key, value)
        pass

    #def set_input_channel_names(input_channel_layout)
    def set_input_channel_layout(channel_layout):
        nonlocal input_channel_layout, input_channel_names, downmix_coefficients
        if not channel_layout:
            return
        print("set_input_channel_layout", repr(channel_layout))
        input_channel_layout = channel_layout
        input_channel_names = input_channel_names_by_layout[input_channel_layout]
        print("set_input_channel_layout input_channel_names", repr(input_channel_names))
        downmix_coefficients = downmix_rfc7845.get_coefficients(input_channel_layout)
        update_scale_dict()

    set_input_channel_layout(input_channel_layout)

    def get_lin_value(value):
        return value/10

    def set_lin_value(value):
        return value*10

    def get_log_value(value):
        return 10**(value/10)

    def update_scale_dict():
        for frame_id in ["volume", "balance"]:
            update_scale_dict_of_frame_id(frame_id)

    def update_scale_dict_of_frame_id(frame_id):
        nonlocal scale_dict, downmix_coefficients
        frame = frame_dict[frame_id]
        #get_value = get_log_value if frame_id == "volume" else get_lin_value
        get_value = get_lin_value
        set_value = set_lin_value
        if frame_id == "volume":
            from_ = 0
            to = 4
            get_init_value = get_channel_volume
        elif frame_id == "balance":
            from_ = -1
            to = 1
            get_init_value = get_channel_balance
        for row_idx, row in enumerate(input_channel_names):
            for col_idx, channel_name in enumerate(row):
                if channel_name == None:
                    continue
                key = f"{frame_id}.{channel_name}"
                init_value = get_init_value(downmix_coefficients, channel_name)
                #print(f"  {key} = {init_value}")
                scale = tk_scale_debounced(
                    frame,
                    channel_name,
                    after_change,
                    on_change=on_change,
                    key=key,
                    get_value=get_value,
                    set_value=set_value,
                    format="%.3f",
                    init_value=init_value,
                    from_=from_,
                    to=to,
                    length=200,
                )
                scale.grid(column=col_idx, row=row_idx, padx=5, pady=5)
                scale_dict[key] = scale

    update_scale_dict()



    #observer_id =
    mpv_ipc_client.bind_property_observer("current-tracks/audio", change_audio_track)

    for track in mpv_ipc_client.track_list:
        if track["type"] != "audio":
            continue
        if track["selected"] == False:
            continue
        #print("audio track:", track)
        id = track["id"]
        channel_layout = track.get("demux-channels") # "stereo", "5.1(side)", ...
        codec = track.get("codec")
        bitrate = track.get("demux-bitrate", 0)
        title = track.get("title")
        print("audio track:", id, channel_layout, codec, bitrate/1000, title)
        #mpv_ipc_client.bind_property_observer(f"track-list/{id}/selected", select_audio_track)

    """
    input_channel_layout = mpv_ipc_client.audio_params["channels"]
    print("input_channel_layout", repr(input_channel_layout))

    downmix_coefficients = downmix_rfc7845.get_coefficients(input_channel_layout)
    print("downmix_coefficients", repr(downmix_coefficients))
    """

    # FIXME
    # change_audio_track channel_layout unknown4
    # audio_params input_channel_layout '3.1'

    audio_params = mpv_ipc_client.audio_params
    if audio_params:
        channel_layout = mpv_ipc_client.audio_params["channels"]
        print("audio_params channel_layout", repr(channel_layout))
        set_input_channel_layout(channel_layout)

        reset_downmix_to_rfc7845()
    else:
        print("mpv_ipc_client.audio_params is empty. waiting for change_audio_track event")



    if show_root_window:
        root_window.mainloop()

    mpv_ipc_client.quit()
    mpv_proc.kill()

    if os.path.exists(mpv_ipc_socket_path):
        os.unlink(mpv_ipc_socket_path)



def exit_handler():
    # fix terminal after running mpv
    # no. "stty sane" fails to reset the terminal cursor
    # stty is part of coreutils
    #subprocess.call(["stty", "sane"])
    # tput is part of ncurses
    subprocess.call(["tput", "init"])



if __name__ == "__main__":
    atexit.register(exit_handler)
    main()

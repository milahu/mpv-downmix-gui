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

# TODO fix terminal after exit, so i dont need "stty sane"

import sys
import os
import subprocess
import socket
import tempfile
import time
import logging

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



input_channel_names_by_layout = dict()

# TODO how does mpv call this? mono?
input_channel_names_by_layout["mono"] = (
    (None, "FC", None),
    (None, None, None),
    (None, None, None),
)

input_channel_names_by_layout["stereo"] = (
    ("FL", None, "FR"),
    (None, None, None),
    (None, None, None),
)

# Linear Surround Channel Mapping: L C R
# left, center, right
# TODO how does mpv call this?
input_channel_names_by_layout["3.0"] = (
    ("FL", "FC", "FR"),
    (None, None, None),
    (None, None, None),
)

# 3.1 Surround Mapping: L C R LFE
# left, center, right, LFE
# 3.0 with LFE
input_channel_names_by_layout["3.1"] = (
    ("FL", "FC", "FR"),
    (None, "LFE", None),
    (None, None, None),
)

# Quadraphonic Channel Mapping: FL FR BL BR
# front left, front right, back left, back right
# TODO how does mpv call this?
input_channel_names_by_layout["quadraphonic"] = (
    ("FL", None, "FR"),
    (None, None, None),
    ("BL", None, "BR"),
)

# 5.0 Surround Mapping: FL FC FR RL RR
# 5.1 without LFE
# front left, front center, front right, back left, back right
input_channel_names_by_layout["5.0"] = (
    ("FL", "FC", "FR"),
    (None, None, None),
    ("BL", None, "BR"),
)

# 5.1 Surround Mapping: FL FC FR RL RR LFE
# front left, front center, front right, back left, back right, LFE
# note: ffmpeg says "back" instead of "rear" -> "BL" instead of "RL"
# mpv calls this "5.1(side)" TODO verify
input_channel_names_by_layout["5.1"] = (
    ("FL", "FC", "FR"),
    (None, "LFE", None),
    ("BL", None, "BR"),
)

# 6.1 Surround Mapping: FL FC FR SL SR BC LFE
# front left, front center, front right, side left, side right, back center, LFE
# 5.1 + back center, "back" -> "side"
input_channel_names_by_layout["6.1"] = (
    ("FL", "FC", "FR"),
    ("SL", "LFE", "SR"),
    (None, "BC", None),
)

# 7.1 Surround Mapping: FL FC FR SL SR BL BR LFE
# front left, front center, front right, side left, side right, back left, back right, LFE
# 6.1 + BC -> BL BR
input_channel_names_by_layout["7.1"] = (
    ("FL", "FC", "FR"),
    ("SL", "LFE", "SR"),
    ("BL", None, "BR"),
)

# 8.1 Surround Mapping: FL FC FR SL SR BL BC BR LFE
# front left, front center, front right, side left, side right, back left, back right, LFE
# 7.1 + BC
input_channel_names_by_layout["8.1"] = (
    ("FL", "FC", "FR"),
    ("SL", "LFE", "SR"),
    ("BL", "BC", "BR"),
)

# TODO more?



def main():

    logging.basicConfig(level=logging.DEBUG)

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

    time.sleep(2) # wait for mpv to create the socket
    mpv_ipc_client = python_mpv_jsonipc.MPV(start_mpv=False, ipc_socket=mpv_ipc_socket_path)

    #print("media_title", mpv_ipc_client.media_title)
    #print("time_pos", mpv_ipc_client.time_pos)
    # audio_params {'samplerate': 48000, 'channel-count': 6, 'channels': '5.1(side)', 'hr-channels': '5.1(side)', 'format': 'floatp'}
    # current_tracks None
    #for key in ["volume", "metadata", "audio_params"]:
    #    print(key, getattr(mpv_ipc_client, key))

    def change_audio_track(name, track):
        #print(name, track)
        if track == None:
            # no audio track
            print("audio track:", None)
            return
        id = track["id"]
        channel_layout = track.get("demux-channels") # "stereo", "5.1(side)", ...
        title = track.get("title")
        #num_channels = track["audio-channels"]
        bitrate = track.get("demux-bitrate", 0)
        codec = track.get("codec")
        print("audio track:", id, channel_layout, codec, bitrate/1000, title)
        # TODO update: input_channel_layout downmix_coefficients scale_dict

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

    input_channel_layout = mpv_ipc_client.audio_params["channels"]
    if input_channel_layout == "5.1(side)": # TODO verify
        input_channel_layout = "5.1"

    print("input_channel_layout", repr(input_channel_layout))

    downmix_coefficients = downmix_rfc7845.get_coefficients(input_channel_layout)
    print("downmix_coefficients", repr(downmix_coefficients))



    # root window
    root = tk.Tk()
    #root.geometry('300x200')
    #root.resizable(False, False)
    root.resizable(True, True)
    #root.title("downmix: " + mpv_ipc_client.media_title)
    root.title("downmix")

    notebook = ttk.Notebook(root)
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

    def reset_downmix_to_rfc7845():
        downmix_coefficients = downmix_rfc7845.get_coefficients(input_channel_layout)
        for channel in downmix_coefficients["FL"]:
            volume = get_channel_volume(downmix_coefficients, channel)
            balance = get_channel_balance(downmix_coefficients, channel)
            scale_dict[f"volume.{channel}"].set(volume)
            scale_dict[f"balance.{channel}"].set(balance)
        after_change()

    option_id = "reset to RFC 7845"
    option = dict()
    options_dict[option_id] = option
    option["button"] = tk.Button(frame, text=option_id, command=reset_downmix_to_rfc7845)
    option["button"].pack()

    def after_change(key=None, value=None):
        for channel in downmix_coefficients["FL"]:
            volume = scale_dict[f"volume.{channel}"].get()
            balance = scale_dict[f"balance.{channel}"].get()
            c = downmix_coefficients
            c["FL"][channel], c["FR"][channel] = \
                get_left_right_coefficient(volume, balance)
        af = downmix_rfc7845.get_ffmpeg_audio_filter(downmix_coefficients)
        assert af.startswith("pan=stereo|FL=")
        print(af)
        # wrap the value in quotes
        af = 'pan="' + af[4:] + '"'
        mpv_ipc_client.af_cmd("set", af)

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

    input_channel_names = input_channel_names_by_layout[input_channel_layout]

    scale_dict = dict()

    def get_lin_value(value):
        return value/10

    def set_lin_value(value):
        return value*10

    def get_log_value(value):
        return 10**(value/10)

    #print("init")
    for frame_id in ["volume", "balance"]:
        frame = frame_dict[frame_id]
        #get_value = get_log_value if frame_id == "volume" else get_lin_value
        get_value = get_lin_value
        set_value = set_lin_value
        if frame_id == "volume":
            from_ = 0
            to = 1
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
                )
                scale.grid(column=col_idx, row=row_idx, padx=5, pady=5)
                scale_dict[key] = scale

    root.mainloop()

    mpv_proc.kill()

    if os.path.exists(mpv_ipc_socket_path):
        os.unlink(mpv_ipc_socket_path)

if __name__ == "__main__":
    main()

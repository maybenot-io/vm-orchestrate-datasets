#!/usr/bin/env python3
# This script checks the dataset for reasonability and validity
# If the prune flag is set, extreme outliers are removed from the dataset
# Otherwise it just warns about how many bad samples are found
import argparse
import os

i = 0


def check(dir, prune):
    global i
    files = os.listdir(dir)

    # lists of all json, png, pcap files for current dir
    json_files = [file for file in files if file.endswith(".json")]
    png_files = [file for file in files if file.endswith(".png")]
    pcap_files = [file for file in files if file.endswith(".pcap")]

    if not png_files or not pcap_files or not json_files:
        print(f"Warning: No PNG or PCAP files found in {dir}.")
        return

    png_sizes = [os.path.getsize(os.path.join(dir, file)) for file in png_files]
    pcap_sizes = [os.path.getsize(os.path.join(dir, file)) for file in pcap_files]

    max_png_size = max(png_sizes, default=0)
    max_pcap_size = max(pcap_sizes, default=0)

    avg_png_size = sum(png_sizes) / len(png_sizes)
    avg_pcap_size = sum(pcap_sizes) / len(pcap_sizes)

    # make sure we have both json, png and pcap for this visit
    for png_file in png_files:
        pcap_file = png_file.replace(".png", ".pcap")
        if pcap_file not in pcap_files:
            print(f"Error: {pcap_file} is missing in {dir}.")
            return
    for png_file in png_files:
        json_file = png_file.replace(".png", ".json")
        if json_file not in json_files:
            print(f"Error: {json_file} is missing in {dir}.")
            return

    # iterate over each visit and check if both png and pcap are valid
    # (size >= 200kb or size within 60-140% of average size)
    for png in png_files:
        json = png.replace(".png", ".json")
        json_size = os.path.getsize(os.path.join(dir, json))
        assert (
            json_size > 0
        )  # Just asserting the json file isn't empty, highly unlikely but why not check.
        png_size = os.path.getsize(os.path.join(dir, png))
        pcap = png.replace(".png", ".pcap")
        pcap_size = os.path.getsize(os.path.join(dir, pcap))
        if not is_ok(
            png_size,
            pcap_size,
            max_png_size,
            max_pcap_size,
            avg_png_size,
            avg_pcap_size,
        ):
            i += 1
            print(
                f"Error: {os.path.join(dir, png)} is not ok (png size: {png_size // 1024}Kb",
                f"max png size: {max_png_size // 1024}Kb, avg png size: {avg_png_size // 1024}Kb",
                f"pcap size: {pcap_size // 1024}Kb, max pcap size {max_pcap_size // 1024}Kb", 
                f"avg pcap size: {avg_pcap_size // 1024}Kb)."
            )
            if prune:  # optionally delete invalid entries, if flag is set
                print(f"Pruning {dir}...")
                os.remove(os.path.join(dir, png))
                os.remove(os.path.join(dir, pcap))
                os.remove(os.path.join(dir, json))


def is_ok(
    png_size,
    pcap_size,
    max_png_size,
    max_pcap_size,
    avg_png_size,
    avg_pcap_size,
    threshold=0.5,
):
    min_size = 1024 * 50  # 50 KB minimum (besides avg)
    max_allowed = 1024 * 1500  # 1500 KB maximum (besides avg)

    def within_range(size, avg):
        return (avg * threshold <= size <= avg * (2 - threshold)) or (
            min_size <= size <= max_allowed
        )

    png_ok = within_range(png_size, avg_png_size)
    pcap_ok = within_range(pcap_size, avg_pcap_size)
    return png_ok and pcap_ok


def main(args):
    print(f"Checking dataset in {args.dir}...")
    print(f"Prune flag: {args.prune}")

    # making sure we entered the right dir and vpnlist
    if not os.path.exists(args.dir):
        print(f"Error: The folder '{args.dir}' does not exist.")
        return

    if not os.path.exists(args.vpnlist):
        print(f"Error: The file '{args.vpnlist}' does not exist.")
        return

    with open(args.vpnlist, "r") as file:
        servers = [line.strip() for line in file if line.strip()]

    # iterate over each server directory
    for server in servers:
        server_path = os.path.join(args.dir, server)
        if not os.path.exists(server_path):
            print(f"Warning: Server directory '{server_path}' does not exist.")
            continue

        # iterate over each url directory for current server
        for subdir in os.listdir(server_path):
            subdir_path = os.path.join(server_path, subdir)
            if os.path.isdir(subdir_path):
                check(subdir_path, args.prune)
    print(f"there are {i} bad files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check dataset.")
    parser.add_argument("--dir", type=str, required=True, help="root folder")
    parser.add_argument(
        "--vpnlist", type=str, required=True, help="path to vpnlist.txt"
    )
    parser.add_argument("--prune", help="prune dataset", action="store_true")

    main(parser.parse_args())

#!/usr/bin/env python3
# This script checks the dataset for reasonability and validity
# If the prune flag is set, extreme outliers are removed from the dataset
# Otherwise it just warns about how many bad samples are found
import argparse
import os

i = 0


def get_server_directories(base_servers, data_dir):
    """
    Auto-detect server directories by looking at which ones exists in the data directory

    Args:
        base_servers: List of base server names from vpnlist
        data_dir: Path to data directory

    Returns:
        List of expected directory names
    """
    print("Auto-detecting server directories from existing data...")

    # Get all existing directories that match our server patterns
    existing_dirs = [
        d for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d))
    ]

    expected_dirs = []

    for server in base_servers:
        # Check if we have non-DAITA version
        if server in existing_dirs:
            expected_dirs.append(server)

        # Check if we have DAITA version
        daita_server = f"{server}_daita"
        if daita_server in existing_dirs:
            expected_dirs.append(daita_server)

    return expected_dirs


def check(dir, prune):
    global i
    files = os.listdir(dir)

    # lists of all json, png, pcap files for current dir
    json_files = [file for file in files if file.endswith(".json")]
    png_files = [file for file in files if file.endswith(".png")]
    pcap_files = [file for file in files if file.endswith(".pcap")]

    if not png_files or not pcap_files or not json_files:
        print(f"Warning: No PNG, PCAP or JSON files found in {dir}.")
        return

    png_sizes = [os.path.getsize(os.path.join(dir, file)) for file in png_files]
    pcap_sizes = [os.path.getsize(os.path.join(dir, file)) for file in pcap_files]

    avg_png_size = sum(png_sizes) / len(png_sizes)
    avg_pcap_size = sum(pcap_sizes) / len(pcap_sizes)

    # make sure we have both a png, pcap and json for each visit
    for png_file in png_files:
        pcap_file = png_file.replace(".png", ".pcap")
        json_file = png_file.replace(".png", ".json")
        if pcap_file not in pcap_files:
            print(f"Error: {pcap_file} is missing in {dir}.")
            return
        if json_file not in json_files:
            print(f"Error: {json_file} is missing in {dir}.")
            return

    # iterate over each visit and check if both png and pcap are valid (size >= 200kb or size within 60-140% of average size)
    for png in png_files:
        pcap = png.replace(".png", ".pcap")
        json = png.replace(".png", ".json")
        png_size = os.path.getsize(os.path.join(dir, png))
        pcap_size = os.path.getsize(os.path.join(dir, pcap))
        if not is_ok(
            png_size,
            pcap_size,
            avg_png_size,
            avg_pcap_size,
        ):
            i += 1
            print(f"Error: {os.path.join(dir, png)} is not ok")
            print(
                f"pcap size: {pcap_size // 1024}Kb, avg pcap size: {avg_pcap_size // 1024}Kb"
            )
            print(
                f"png size: {png_size // 1024}Kb, avg png size: {avg_png_size // 1024}Kb"
            )
            if prune:  # optionally delete the files related to the invalid sample, if flag is set
                print(f"Pruning {dir}...")
                os.remove(os.path.join(dir, png))
                os.remove(os.path.join(dir, pcap))
                os.remove(os.path.join(dir, json))


def is_ok(
    png_size,
    pcap_size,
    avg_png_size,
    avg_pcap_size,
    threshold=0.5,
):
    min_size = 1024 * 50  # 50 KiB minimum (besides avg)
    max_allowed = 1024 * 3000  # 3 MiB maximum (besides avg)

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
        base_servers = [line.strip() for line in file if line.strip()]

    server_dirs = get_server_directories(base_servers, args.dir)

    # iterate over each server directory
    for server_dir in server_dirs:
        server_path = os.path.join(args.dir, server_dir)

        # iterate over each url directory for current server
        for subdir in os.listdir(server_path):
            subdir_path = os.path.join(server_path, subdir)
            if os.path.isdir(subdir_path):
                check(subdir_path, args.prune)

    print(f"there are {i} bad files")
    # Anything over 0 bad files is a bad exit
    exit(i)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check dataset.")
    parser.add_argument("--dir", type=str, required=True, help="root folder")
    parser.add_argument(
        "--vpnlist", type=str, required=True, help="path to vpnlist.txt"
    )

    parser.add_argument("--prune", help="prune dataset", action="store_true")

    main(parser.parse_args())

#!/usr/bin/env python3
import argparse
import os
import sys
import multiprocessing
from scapy.all import PcapReader
from datetime import datetime


def main(args):
    print(f"goal is {args.classes} classes, {args.samples} samples per class")
    print(f"results folder {args.results}")

    if os.path.exists(args.results):
        sys.exit(f"the results folder {args.results} already exists")

    print(f"Checking dataset structure in {args.dir}...")
    for c in range(args.classes):
        for s in range(args.samples):
            pcap = os.path.join(args.dir, f"{c}", f"{s}.pcap")
            if not os.path.exists(pcap):
                sys.exit(f"{pcap} is missing.")
    print("inout dataset structure contains necessary files")

    print(f"creating results folder {args.results}...")
    os.mkdir(args.results)
    for c in range(args.classes):
        os.makedirs(os.path.join(args.results, f"{c}"))

    tasks = []
    with multiprocessing.Pool() as pool:
        for c in range(args.classes):
            for s in range(args.samples):
                pcap_path = os.path.join(args.dir, f"{c}", f"{s}.pcap")
                pcap_dest = os.path.join(args.results, f"{c}", f"{s}.log")
                tasks.append(pool.apply_async(parse_pcap, args=(pcap_path, pcap_dest)))

        for task in tasks:
            task.get()


def parse_pcap(pcap_file, trace_file):
    print(f"parse {pcap_file} to {trace_file}")
    first_timestamp = None
    lines = []

    try:
        capture = PcapReader(pcap_file)
        for packet in capture:
            if first_timestamp is None and packet.time:
                first_timestamp = datetime.fromtimestamp(float(packet.time))

            parsed_packet = parse_packet(packet, first_timestamp)
            if parsed_packet:
                lines.append(parsed_packet)
    except Exception as e:
        print(f"error processing pcap file: {e}")

    with open(trace_file, "w") as f:
        f.write("\n".join(lines))


def parse_packet(packet, first_timestamp):
    if packet.haslayer("IP") and hasattr(packet, "time") and first_timestamp:
        src_ip = packet["IP"].src
        dir = "s" if src_ip.startswith("192.168") else "r"
        timestamp = datetime.fromtimestamp(float(packet.time))
        duration = timestamp - first_timestamp
        # Convert to nanoseconds, but make sure it's not negative
        timestamp = max(0, duration.total_seconds() * 1000 * 1000 * 1000)

        return f"{timestamp:.0f},{dir},{packet['IP'].len}"
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="create a dataset of traces from a raw collected dataset"
    )
    parser.add_argument(
        "--dir", required=True, help="root folder of collected raw data"
    )
    parser.add_argument(
        "--results",
        required=True,
        help="folder to create with results (mirroring structure)",
    )
    parser.add_argument(
        "--classes", required=True, type=int, help="number of expected classes"
    )
    parser.add_argument(
        "--samples",
        required=True,
        type=int,
        help="number of expected samples per class",
    )

    main(parser.parse_args())

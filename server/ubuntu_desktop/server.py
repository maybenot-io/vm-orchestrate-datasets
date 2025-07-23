#!/usr/bin/env python3
from flask import Flask, request, jsonify
import threading
import requests
import random
import argparse
import os, sys
import time
import json
import re

# shared states, protected by lock
accounts = []
allocated_accounts = {}
vpn_server_list = []
done_dict = {}
pending_visits = []
url2line = {}
datadir = None
samples = None
visits = None
starting_time = None
last_update_time = None
unique_clients = set()
lock = threading.Lock()

app = Flask(__name__)

@app.route('/')
def hello():
    return "hello world - here be a data collection server\nAvailable endpoints: /server [GET], /setup [GET], /status [GET], /work [GET,POST]\n"

@app.route('/setup', methods=['GET'])
def setup():
    global allocated_accounts, visits
    with lock:
        id = request.args.get('id', default="*", type=str)
        if id == "*":
            return jsonify({"error": "missing id"}), 400
        if id in allocated_accounts:
            return jsonify({
            "account": allocated_accounts[id],
            "visit_count": visits
        }), 200
        try:
            allocated_accounts[id] = accounts.pop()
            return jsonify({
                "account": allocated_accounts[id],
                "visit_count": visits
            }), 200
        except:
            return jsonify({"error": "no available accounts remain"}), 400

@app.route('/status', methods=['GET'])
def status():
    global done_dict, starting_time, last_update_time, unique_clients, allocated_accounts, vpn_server_list, url2line, lock

    with lock:
        total_collected = 0
        for vpn in vpn_server_list:
            total_collected += sum(done_dict[vpn].values())
        return jsonify({
            "total_to_collect": samples * len(url2line) * len(vpn_server_list),
            "total_collected": total_collected,
            "elapsed": time.time() - starting_time,
            "last_update": time.time() - last_update_time,
            "unique_clients": list(unique_clients),
            "allocated_accounts": f"{len(allocated_accounts)} of {len(accounts)}",
        })

@app.route('/server', methods=['GET'])
def get_server():
    global unique_clients, pending_visits, lock

    with lock:
        id = request.args.get('id', default="*", type=str)
        server = request.args.get('server', default="*", type=str)
        if not id or not server:
            return ("missing id or previous server", 400)
        unique_clients.add(id)

        # Client wants new server, so we return a random one from pending visits
        # But we ensure that the server is not the same as the previous one used
        # unless it is the only one available / remaining
        available_servers = {visit['vpn'] for visit in pending_visits}
        if len(available_servers) > 1 and server in available_servers:
            available_servers.discard(server)
        if not available_servers:
            return jsonify({"error": "no VPN servers available"}), 400
        return jsonify(random.choice(list(available_servers)))

@app.route('/work', methods=['GET'])
def get_work():
    global unique_clients, pending_visits, lock

    with lock:
        id = request.args.get('id', default="*", type=str)
        server = request.args.get('server', default="*", type=str)
        if id == '*':
            return ("missing id", 400)
        unique_clients.add(id)

        # Only return work for the specific server being used by the client right now
        if server != '*':
            filtered_visits = [visit for visit in pending_visits if visit["vpn"] == server]
            if not filtered_visits:
                return jsonify({"error": "no links left to visit"}), 400
        else:
            filtered_visits = pending_visits
        return jsonify(random.choice(filtered_visits))


@app.route('/work', methods=['POST'])
def post_work():
    global done_dict, datadir, samples, last_update_time, unique_clients, pending_visits, lock

    id = request.form.get('id')                 # unique identifier per client
    url = request.form.get('url')               # url visited
    vpn = request.form.get('vpn')               # name of vpn server used
    png_data = request.form.get('png_data')     # hex-encoded PNG file
    pcap_data = request.form.get('pcap_data')   # hex-encoded PCAP file
    metadata = request.form.get('metadata')     # metadata about the visit (QoE, timestamp, etc.)

    if not id or not url or not png_data or not pcap_data or not vpn or not metadata:
        return "missing one or more required fields", 400

    print("Received work for url: ", url, 'from', id)

    try:
        png_data = bytes.fromhex(png_data)
        pcap_data = bytes.fromhex(pcap_data)
    except:
        return "failed to decode hex-encoded data", 400

    png_kib = len(png_data)/1024
    pcap_kib = len(pcap_data)/1024

    print(f"Got {png_kib:.1f} KiB of PNG data")
    print(f"Got {pcap_kib:.1f} KiB of pcap data")

    # we report 200 here because the client did its reporting, just that the
    # data was too small or large so we won't save it and repeat the visit
    if pcap_kib < 3 or pcap_kib > 1500:
        return ("pcap data too small, but OK", 200)
    if png_kib < 3:
        return ("png data too small, but OK", 200)

    print('Saving the sample..')
    with lock:
        if done_dict[vpn][url] >= samples:
            return ("Already done, but OK", 200)

        # save to disk: datadir/f"{vpn_dir}/{url2line[url]}".{png,pcap}
        site = url2line[url]
        sample = get_free_sample(site, vpn)
        p = os.path.join(datadir, vpn, f"{str(site)}", f"{sample}")
        with open(f"{p}.png", 'wb') as f:
            f.write(png_data)
        with open(f"{p}.pcap", 'wb') as f:
            f.write(pcap_data)
        with open(f"{p}.json", 'w') as f:
            f.write(json.dumps(json.loads(metadata), indent=2))
        # increment and see if all visits for this combo is done
        done_dict[vpn][url] += 1
        visit = {"url": url, "vpn": vpn}
        if done_dict[vpn][url] >= samples and visit in pending_visits:
            print(f"Done with {url} for {vpn}, there are now {len(pending_visits) - 1} combinations left")
            pending_visits.remove(visit)

        last_update_time = time.time()

    return ("OK\n", 200)

def get_free_sample(site, vpn) -> int:
    global datadir, vpn_server_list
    sample = 0
    while True:
        p = os.path.join(datadir, vpn, f"{str(site)}", f"{sample}.png")
        if not os.path.exists(p):
            return sample
        sample += 1

def setup_url_list(url_list) -> None:
    global done_dict, vpn_server_list, url2line

    urls = []
    with open(url_list, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            urls.append(line)

    # requirement: all URLs must be unique
    if len(urls) != len(set(urls)):
        print("URL list contains duplicates, exiting")
        print(f"URL length: {len(urls)}")
        print(f"URL set length: {len(set(urls))}")
        sys.exit(1)
    # requirement: all URLs must be HTTP(S)
    for url in urls:
        if not url.startswith("http://") and not url.startswith("https://"):
            print(f"URL {url} is not HTTP(S), exiting")
            sys.exit(1)

    for vpn in vpn_server_list:
        for (i, url) in enumerate(urls):
            url2line[url] = i
            done_dict[vpn][url] = 0

    print(f"Loaded {len(urls)} URLs from {url_list}")

def setup_vpn_list(vpn_list) -> None:
    global vpn_server_list, done_dict

    vpns = []
    with open(vpn_list, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            vpns.append(line)

    # Assert all servers entered actually support DAITA
    mullvad_servers = requests.get("https://api.mullvad.net/app/v1/relays").json()['wireguard']['relays']
    invalid_servers = [
        vpn_name for vpn_name in vpns if not any(server['hostname'] == vpn_name for server in mullvad_servers)
    ]

    if invalid_servers:
        print(f"The following server-names are not valid: {', '.join(invalid_servers)}")
        sys.exit(1)
    vpn_server_list = vpns.copy()
    for vpn in vpn_server_list:
        done_dict[vpn] = dict()

def setup_datadir(dir) -> None:
    global datadir, url2line, samples, done_dict, vpn_server_list
    total_to_collect = samples * len(url2line) * len(vpn_server_list)
    total_samples = 0

    if os.path.exists(dir):
        if not os.access(dir, os.W_OK):
            print(f"datadir {dir} is not writable, exiting")
            sys.exit(1)

        for vpn in vpn_server_list:
            vpn_dir = os.path.join(dir, vpn)
            if not os.path.exists(vpn_dir):
                print(f"{vpn_dir} is missing, exiting")
                sys.exit(1)
            if not os.access(vpn_dir, os.W_OK):
                print(f"{vpn_dir} is not writable, exiting")
                sys.exit(1)
            all_files = [
                [f for f in files if not f.startswith(".")]
                for root, _, files in sorted(
                    os.walk(vpn_dir),
                    key=lambda x: int(re.search(r"(\d+)$", x[0]).group(1))
                    if re.search(r"(\d+)$", x[0]) else float("inf")
                )
            ]

            done = [len(files) for files in all_files]
            if sum(done) % 3 != 0:
                print(f"{vpn_dir} (off) does not contain a multiple of 3 files, exiting")
                sys.exit(1)

            png_files = [[f for f in files if f.endswith(".png")] for files in all_files]

            for i, pnglist in enumerate(png_files):
                pcaplist = [png.replace(".png", ".pcap") for png in pnglist]
                if any(pcap not in all_files[i] for pcap in pcaplist):
                    print(f"{vpn_dir} (off) - a pcap file is missing, exiting")
                    sys.exit(1)
            
            # each sample consists of a PNG, PCAP and JSON file so we do // 3 for each of these
            for i, url in enumerate(done_dict[vpn]):
                done_dict[vpn][url] = done[i] // 3
            total_samples += sum(done) // 3

        print(f"datadir {dir} exists, contains {total_samples} samples, {total_to_collect - total_samples} to go")
    else:
        os.mkdir(dir)
        # create subdirs: one per server, one per site/line per server
        for vpn in vpn_server_list:
            os.mkdir(os.path.join(dir, vpn))
            for url in done_dict[vpn]:
                os.mkdir(os.path.join(dir, vpn, f"{url2line[url]}"))

        print(f"datadir {dir} created, contains 0 samples, {total_to_collect} to go")

    datadir = dir

def setup_database(database_file) -> None:
    global accounts

    with open(database_file, 'r') as file:
        d = json.load(file)
        accounts = d["accounts"]

        # randomize the accounts
        random.shuffle(accounts)

    print(f"Loaded {len(accounts)} accounts from {database_file}")

def setup_visit_list():
    global pending_visits, done_dict, samples

    pending_visits = [
        {"url": url, "vpn": vpn}
        for vpn in done_dict
        for url, count in done_dict[vpn].items()
        if count < samples
    ]

def main(args) -> None:
    global samples, starting_time, last_update_time, visits
    if not 0 < args.samples < 1000:
        print(f"samples must be in range 0 < x < 1000, exiting")
        sys.exit(1)
    samples = args.samples
    visits = args.visits

    setup_vpn_list(args.vpnlist)
    setup_url_list(args.urllist)
    setup_datadir(args.datadir)
    setup_database(args.database)
    setup_visit_list()

    starting_time = time.time()
    last_update_time = starting_time
    app.run(debug=False, threaded=True, port=args.port, host=args.host)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Run a data collection server.")
    parser.add_argument("--datadir", type=str, required=True, help="Directory to store data in.")
    parser.add_argument("--urllist", type=str, required=True, help="List of URLs to collect data for.")
    parser.add_argument("--vpnlist", type=str, required=True, help="List of VPNs relays to use")
    parser.add_argument("--samples", type=int, default=100, help="Number of samples to collect for each URL.")
    parser.add_argument("--visits", type=int, default=10, help="Number of visits to perform per VPN connection.")
    parser.add_argument("--host", type=str, default="192.168.100.1", help="Host to listen on.")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on.")
    parser.add_argument("--database", type=str, required=True, help="File with mullvad account information.")

    args = parser.parse_args()
    main(args)
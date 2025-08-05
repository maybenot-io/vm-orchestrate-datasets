#!/usr/bin/env python3
"""
Data Collection Server for VPN-based web browsing experiments
Handles client coordination, work distribution, and result collection
"""

import json
import random
import sys
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, request


class DataCollectionServer:
    def __init__(self, config_path="env/config.json"):
        """
        Initialize the data collection server

        Args:
            config_path (str): Path to JSON configuration file
        """
        # Create Flask application instance
        self.app = Flask(__name__)

        # Thread lock for thread-safe operations
        self.lock = threading.Lock()

        # Load configuration from file
        self.config = self._load_config(config_path)

        # Initialize server state variables
        self._initialize_state()

        # Set up Flask routes/endpoints
        self._setup_routes()

    def _load_config(self, config_path):
        """
        Load and validate server configuration from JSON file

        Args:
            config_path (str): Path to configuration file

        Returns:
            dict: Loaded configuration
        """
        try:
            with open(config_path) as f:
                config = json.load(f)

            # Verify all required server configurations exist
            required_server = ["datadir", "urllist", "vpnlist", "database"]
            for key in required_server:
                if key not in config.get("server", {}):
                    raise ValueError(f"Missing required server config: {key}")
                # Validate file paths exist (except datadir which we'll create)
                if key != "datadir" and not Path(config["server"][key]).exists():
                    raise FileNotFoundError(
                        f"Config path does not exist: {config['server'][key]}"
                    )

            # Set default values if not specified
            config.setdefault(
                "server",
                {
                    "samples": 5,  # Default number of samples per URL
                    "visits": 10,  # Default number of visits per VPN connection
                    "host": "192.168.100.1",  # Default server host
                    "port": 5000,  # Default server port
                },
            )
            config.setdefault(
                "timing",
                {
                    "grace": 5,  # Additional wait time after page load
                    "min_wait": 20,  # Minimum time to spend on each page
                    "max_wait": 30,  # Maximum time to spend on each page
                    "post_browser_pre_capture_wait": 5,  # Grace after starting browser, before starting capture
                    "post_packet_pre_visit_wait": 5,  # Grace after starting capture, before performing visit
                },
            )
            config.setdefault(
                "client",
                {
                    "display_size": [
                        1920,
                        1080,
                    ],  # Default display size for headless browser
                    "fullscreen": True,  # Default to fullscreen mode
                    "daita": "off",  # Default to not using DAITA if not set
                },
            )

            return config
        except Exception as e:
            print(f"[ERROR] Config loading failed: {e}")
            sys.exit(1)

    def _initialize_state(self):
        """Initialize all server state variables and data structures"""
        self.accounts = []  # Available VPN accounts
        self.allocated_accounts = {}  # Accounts assigned to clients
        self.vpn_server_list = []  # List of available VPN servers
        self.done_dict = {}  # Tracks completed samples per VPN/URL/DAITA combo
        self.pending_visits = []  # Work still needing completion
        self.url2line = {}  # Maps URLs to line numbers in urllist
        self.unique_clients = set()  # Tracks connected client IDs
        self.starting_time = time.time()  # Server start timestamp
        self.last_update_time = self.starting_time  # Last data update timestamp

        # Load all required data files and initialize structures
        self._load_urls()  # Load URL list to visit
        self._load_vpn_servers()  # Load VPN server list
        self._init_data_directory()  # Set up data directory structure
        self._load_progress_from_current_data()  # Load current progress, if any
        self._load_accounts()  # Load VPN accounts
        self._init_visit_list()  # Initialize work queue

    def _setup_routes(self):
        """Register all Flask route handlers"""
        # Basic info endpoint
        self.app.route("/")(self.hello)

        # Client setup endpoint
        self.app.route("/setup", methods=["GET"])(self.setup_client)

        # Server status endpoint
        self.app.route("/status", methods=["GET"])(self.get_status)

        # VPN server assignment endpoint
        self.app.route("/server", methods=["GET"])(self.get_vpn_server)

        # Work distribution endpoints
        self.app.route("/work", methods=["GET"])(self.get_work)
        self.app.route("/work", methods=["POST"])(self.post_work)

    def hello(self):
        """
        Root endpoint showing available routes

        Returns:
            str: HTML formatted welcome message
        """
        return (
            "<h2>👋 Welcome to the Data Collection Server!</h2>"
            "<p>Available endpoints:</p>"
            "<ul>"
            "<li><b>/setup</b> (GET) - Get your VPN account and config info</li>"
            "<li><b>/status</b> (GET) - See server status and progress</li>"
            "<li><b>/server</b> (GET) - Get a VPN server to use</li>"
            "<li><b>/work</b> (GET/POST) - Get or submit your assigned work</li>"
            "</ul>"
            "<pre>"
            "        ──────▄▌▐▀▀▀▀▀▀▀▀▀▀▀▌\n"
            "        ───▄▄██▌█ Data      █\n"
            "        ▄▄▄▌▐██▌█ Collection█\n"
            "        ███████▌█   Time!   █\n"
            "        ▀❍▀▀▀▀▀▀▀▀▀▀▀▀▀▀❍▀▀▀\n"
            "</pre>"
        )

    def _load_urls(self):
        """Load and validate URLs from config file"""
        with open(self.config["server"]["urllist"]) as f:
            urls = [line.strip() for line in f if line.strip()]

        if len(urls) != len(set(urls)):
            print("[ERROR] URL list contains duplicates")
            sys.exit(1)

        for url in urls:
            if not url.startswith(("https://")):
                print(f"[ERROR] Invalid URL (must be HTTPS): {url}")
                sys.exit(1)

        self.url2line = {url: i for i, url in enumerate(urls)}

    def _load_vpn_servers(self):
        """Load and validate VPN servers from config file"""
        with open(self.config["server"]["vpnlist"]) as f:
            vpns = [line.strip() for line in f if line.strip()]

        try:
            mullvad_servers = requests.get(
                "https://api.mullvad.net/app/v1/relays"
            ).json()["wireguard"]["relays"]
            invalid = [
                v for v in vpns if not any(s["hostname"] == v for s in mullvad_servers)
            ]

            if invalid:
                print(f"[ERROR] Invalid servers: {', '.join(invalid)}")
                sys.exit(1)
            self.vpn_server_list = vpns
        except Exception as e:
            print(f"[ERROR] Failed to validate VPN servers: {e}")
            sys.exit(1)

    def _init_data_directory(self):
        """Initialize data directory structure"""
        datadir = Path(self.config["server"]["datadir"])
        daita_modes = (
            ["on", "off"]
            if self.config["client"].get("daita", "off") == "on"
            else ["off"]
        )

        if not datadir.exists():
            # Create fresh directory structure
            print(f"[INIT] Creating new data directory at {datadir}")
            datadir.mkdir()

            # Create subdirectories for each VPN server, URL and/or DAITA combination
            for vpn in self.vpn_server_list:
                vpn_dir = datadir / vpn
                vpn_dir.mkdir()
                for url in self.url2line:
                    site_num = self.url2line[url]
                    for daita in daita_modes:
                        path = vpn_dir / f"{site_num}_{daita}"
                        path.mkdir(parents=True)
        else:
            print(f"[INIT] Using existing data directory at {datadir}")
            missing, unexpected = [], []

            for vpn in self.vpn_server_list:
                vpn_dir = datadir / vpn
                if not vpn_dir.exists():
                    missing.append(str(vpn_dir))
                    continue
                for url, site_num in self.url2line.items():
                    for mode in daita_modes:
                        if not (vpn_dir / f"{site_num}_{mode}").exists():
                            missing.append(f"{vpn}/{site_num}_{mode}")
                    if daita_modes == ["off"] and (vpn_dir / f"{site_num}_on").exists():
                        unexpected.append(f"{vpn}/{site_num}_on")

            if missing or unexpected:
                print(" Missing:", ", ".join(missing))
                print(" Unexpected:", ", ".join(unexpected))
                raise RuntimeError("Invalid data directory structure. Exiting.")

            print("[INIT] Directory structure validated.")

    def _load_progress_from_current_data(self):
        """Scan data directory and update self.done_dict with completed sample counts"""
        data_dir = Path(self.config.get("server").get("datadir"))
        daita = self.config["client"].get("daita", "off")
        daita_modes = ["on", "off"] if daita == "on" else ["off"]

        # Initialize done_dict[vpn][url][daita] = 0
        self.done_dict = {
            vpn: {
                url: {mode: 0 for mode in daita_modes} for url in self.url2line.keys()
            }
            for vpn in self.vpn_server_list
        }

        line2url = {v: k for k, v in self.url2line.items()}
        for vpn_dir in data_dir.iterdir():
            if not vpn_dir.is_dir():
                continue
            vpn = vpn_dir.name

            for subdir in vpn_dir.iterdir():
                if not subdir.is_dir():
                    continue

                try:
                    name_parts = subdir.name.split("_")
                    if len(name_parts) != 2:
                        continue

                    site_num, daita_mode = name_parts
                    if daita_mode not in daita_modes:
                        continue

                    site_num = int(site_num)
                    if site_num not in line2url:
                        # site_num not recognized, skip
                        continue
                    url = line2url[site_num]
                    count = len(list(subdir.glob("*.json")))
                    self.done_dict[vpn][url][daita_mode] = count

                except Exception as e:
                    print(f"[WARN] Skipped invalid directory '{subdir}': {e}")
                    continue
        print("[INIT] Loaded existing progress from files.")

    def _load_accounts(self):
        """Load VPN accounts from database file"""
        try:
            with open(self.config["server"]["database"]) as f:
                self.accounts = json.load(f)["accounts"]

            # Randomize account order for distribution
            random.shuffle(self.accounts)
            print(f"[INIT] Loaded {len(self.accounts)} VPN accounts")
        except Exception as e:
            print(f"[ERROR] Failed to load accounts: {e}")
            sys.exit(1)

    def _init_visit_list(self):
        """Initialize pending visits tracking"""
        daita = self.config["client"].get("daita", "off")
        daita_modes = ["on", "off"] if daita == "on" else ["off"]
        self.pending_visits = [
            {"url": url, "vpn": vpn, "daita": daita_mode}
            for vpn in self.done_dict
            for url in self.done_dict[vpn]
            for daita_mode in daita_modes
            if self.done_dict[vpn][url][daita_mode]
            < self.config["server"].get("samples", 100)
        ]
        print(f"[INIT] Initialized with {len(self.pending_visits)} combinations left")

    def setup_client(self):
        """
        Endpoint for client setup

        Returns:
            JSON: Account info and client configuration parameters or error message
        """
        id = request.args.get("id")
        if not id:
            return jsonify({"error": "Client ID required"}), 400

        with self.lock:
            if id in self.allocated_accounts:
                # Client already has an account
                account = self.allocated_accounts[id]
                print(f"[CLIENT] Returning existing account for {id}")
            else:
                try:
                    # Assign new account to client
                    account = self.accounts.pop()
                    self.allocated_accounts[id] = account
                    print(f"[CLIENT] Assigned new account to {id}")
                except IndexError:
                    print(f"[ERROR] No accounts available for {id}")
                    return jsonify({"error": "No available accounts"}), 400

            return jsonify(
                {
                    "account": account,
                    "visit_count": self.config["server"].get("visits", 10),
                    "grace": self.config["timing"].get("grace", 1),
                    "min_wait": self.config["timing"].get("min_wait", 2),
                    "max_wait": self.config["timing"].get("max_wait", 30),
                    "display_size": self.config["server"].get(
                        "display_size", [1920, 1080]
                    ),
                    "fullscreen": self.config["server"].get("fullscreen", True),
                    "post_browser_pre_capture_wait": self.config.get(
                        "post_browser_pre_capture_wait", 5
                    ),
                    "post_packet_pre_visit_wait": self.config.get(
                        "post_packet_pre_visit_wait", 5
                    ),
                }
            )

    def get_status(self):
        """
        Endpoint for server status information

        Returns:
            JSON: Server status including collection progress
        """
        with self.lock:
            # Calculate total collected samples (across all daita modes)
            total_collected = sum(
                sum(mode_count for mode_count in url_dict.values())
                for vpn_dict in self.done_dict.values()
                for url_dict in vpn_dict.values()
            )

            daita = self.config["client"].get("daita", "off")
            daita_multiplier = 2 if daita == "on" else 1

            return jsonify(
                {
                    "total_to_collect": self.config["server"].get("samples", 100)
                    * len(self.url2line)
                    * len(self.vpn_server_list)
                    * daita_multiplier,
                    "total_collected": total_collected,
                    "elapsed": time.time() - self.starting_time,
                    "last_update": time.time() - self.last_update_time,
                    "unique_clients": list(self.unique_clients),
                    "allocated_accounts": f"{len(self.allocated_accounts)}/{len(self.accounts)}",
                }
            )

    def get_vpn_server(self):
        """
        Endpoint to get VPN server assignment
        Returns a VPN server + DAITA combination different
        from the client's current one if possible

        Returns:
            JSON: VPN server hostname and daita mode, or error message
        """
        client_id = request.args.get("id")
        current_server = request.args.get("server")
        current_daita = request.args.get("daita")

        if not client_id or not current_server or not current_daita:
            return jsonify({"error": "Client ID and current server/daita combination required"}), 400

        with self.lock:
            self.unique_clients.add(client_id)

            # Build set of available (vpn, daita) pairs with pending work
            available = {(v["vpn"], v["daita"]) for v in self.pending_visits}

            # Remove current server+daita tuple if present and if there is more than one option
            current = (current_server, current_daita)
            if len(available) > 1 and current in available:
                available.remove(current)

            if not available:
                print(f"[CLIENT] No VPN servers available for {client_id}")
                return jsonify({"error": "No VPN servers available"}), 400

            assigned_server = random.choice(list(available))
            print(f"[CLIENT] Assigned VPN server {assigned_server} to {client_id}")

            response_obj = {"vpn": assigned_server[0], "daita": assigned_server[1]}
            return jsonify(response_obj)

    def get_work(self):
        """
        Endpoint to get work assignment
        Returns a URL to visit with the specified VPN server and DAITA mode

        Returns:
            JSON: Work assignment (URL + VPN + DAITA) or error message
        """
        client_id = request.args.get("id")
        requested_server = request.args.get("server")
        requested_daita = request.args.get("daita")

        if not client_id or not requested_server or not requested_daita:
            return jsonify({"error":"Missing required fields"}), 400

        if requested_server == "None":
            print(
                f"[CLIENT] 'None' server supplied from {client_id} - should setup for new"
            )
            return jsonify({"error": "None supplied as server - go fetch new"}), 409

        with self.lock:
            self.unique_clients.add(client_id)
            visits = self.pending_visits

            visits = [v for v in visits if v["vpn"] == requested_server]
            visits = [v for v in visits if v.get("daita") == requested_daita]

            if not visits:
                print(
                    f"[CLIENT] No work available for {client_id}",
                    f"(vpn={requested_server}, daita={requested_daita})",
                )
                return jsonify({"error": "No work available for this combination"}), 409

            assignment = random.choice(visits)
            print(
                f"[CLIENT] Assigned work to {client_id}: {assignment['url']}",
                f"via {assignment['vpn']} with daita {assignment.get('daita')}",
            )

            # Return only the needed keys explicitly
            return jsonify(
                {
                    "url": assignment.get("url"),
                    "vpn": assignment.get("vpn"),
                    "daita": assignment.get("daita"),
                }
            )

    def post_work(self):
        """
        Endpoint to submit work results
        Handles screenshot (PNG), network capture (PCAP), and metadata

        Returns:
            JSON: Success/error status
        """
        required_fields = [
            "id",
            "url",
            "vpn",
            "daita",
            "png_data",
            "pcap_data",
            "metadata",
        ]
        if any(f not in request.form for f in required_fields):
            print("[POST] Missing required fields in submission")
            return jsonify({"error":"Missing required fields"}), 400

        try:
            png_data = bytes.fromhex(request.form["png_data"])
            pcap_data = bytes.fromhex(request.form["pcap_data"])
        except Exception as e:
            print(f"[POST] Invalid hex data: {e}")
            return jsonify({"error":"Invalid hex dataa"}), 400

        client_id = request.form["id"]
        url = request.form["url"]
        vpn = request.form["vpn"]
        daita = request.form["daita"]

        png_size = len(png_data)
        pcap_size = len(pcap_data)

        print(f"\n[POST] Received work from {client_id}:")
        print(f"  URL: {url}")
        print(f"  VPN: {vpn}")
        print(f"  DAITA: {daita}")
        print(f"  PNG size: {png_size:,} bytes ({png_size / 1024:.1f} KiB)")
        print(f"  PCAP size: {pcap_size:,} bytes ({pcap_size / 1024:.1f} KiB)")

        if pcap_size < 10 * 1024 or pcap_size > 3000 * 1024:
            print(f"[POST] Rejected: PCAP size {pcap_size} out of bounds")
            return jsonify({"error":"PCAP size invalid"}), 200
        if png_size < 10 * 1024:
            print(f"[POST] Rejected: PNG too small ({png_size} bytes)")
            return jsonify({"error":"PNG size invalid"}), 200

        with self.lock:
            # Check if already completed for this (vpn, url, daita)
            current_count = self.done_dict[vpn][url].get(daita, 0)
            max_samples = self.config["server"].get("samples", 100)
            if current_count >= max_samples:
                print(
                    f"[POST] Rejected: Already completed {current_count} samples "
                    f"for {url} via {vpn} with daita={daita}"
                )
                return jsonify({"error":f"URL already has maximum of {max_samples} samaples"}), 200

            # Determine where to save
            site_num = self.url2line[url]
            base_dir = (
                Path(self.config["server"]["datadir"]) / vpn / f"{site_num}_{daita}"
            )
            sample_num = self._get_free_sample_num(vpn, daita, site_num)
            base_path = base_dir / str(sample_num)

            try:
                base_path.with_suffix(".png").write_bytes(png_data)
                base_path.with_suffix(".pcap").write_bytes(pcap_data)
                base_path.with_suffix(".json").write_text(request.form["metadata"])
                print(f"[POST] Saved sample #{sample_num} to {base_path}")
            except Exception as e:
                print(f"[ERROR] Failed to save files: {e}")
                return jsonify({"error":"Failed to save the data"}), 500

            # Update sample count
            self.done_dict[vpn][url][daita] = current_count + 1

            # Remove from pending if needed
            if self.done_dict[vpn][url][daita] >= max_samples:
                visit = {"url": url, "vpn": vpn, "daita": daita}
                if visit in self.pending_visits:
                    self.pending_visits.remove(visit)
                    print(
                        f"[POST] Completed all samples for {url} via {vpn} (daita={daita})"
                    )

            self.last_update_time = time.time()

        print(f"[POST] Successfully processed sample from {client_id}")
        return jsonify(
            {
                "status": "OK",
                "message": f"Saved sample #{sample_num} for {url} via {vpn} (daita={daita})",
            }
        ), 200

    def _get_free_sample_num(self, vpn, daita, site_num):
        """
        Find next available sample number for a site

        Args:
            vpn (str): VPN server name
            site_num (int): Site line number

        Returns:
            int: Next available sample number
        """
        dir_path = Path(self.config["server"]["datadir"]) / vpn / f"{site_num}_{daita}"

        # Get all existing sample numbers
        existing = [int(f.stem) for f in dir_path.glob("*") if f.stem.isdigit()]

        # Return next available number
        return max(existing) + 1 if existing else 0

    def run(self):
        """
        Start the Flask server
        Uses host and port from configuration
        """
        host = self.config["server"].get("host", "192.168.100.1")
        port = self.config["server"].get("port", 5000)

        print(f"\n[SERVER] Starting on {host}:{port}")
        print(f"[SERVER] Samples per URL: {self.config['server'].get('samples', 100)}")
        print(
            f"[SERVER] Visits per VPN connection: {self.config['server'].get('visits', 10)}"
        )
        print(f"[SERVER] Timing - Grace: {self.config['timing'].get('grace', 5)}s")
        print(
            f"[SERVER] Timing - Min Wait: {self.config['timing'].get('min_wait', 20)}s"
        )
        print(
            f"[SERVER] Timing - Max Wait: {self.config['timing'].get('max_wait', 30)}s"
        )
        print(
            f"[SERVER] Timing - post browser start, pre capture start: {
                self.config['timing'].get('post_browser_pre_capture_wait', 5)
            }s"
        )
        print(
            f"[SERVER] Timing - post capture start, pre visit: {
                self.config['timing'].get('post_packet_pre_visit_wait', 5)
            }s"
        )

        self.app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    # Create and run server instance
    server = DataCollectionServer()
    server.run()

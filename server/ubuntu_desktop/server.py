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
    MIN_PCAP_SIZE = 10 * 1024  # 10 KiB
    MAX_PCAP_SIZE = 3000 * 1024  # 3 MiB
    MIN_PNG_SIZE = 10 * 1024  # 10 KiB

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
            self._validate_config(config)
            self._set_config_defaults(config)
            return config
        except Exception as e:
            print(f"[ERROR] Config loading failed: {e}")
            sys.exit(1)

    def _validate_config(self, config: dict) -> None:
        """Verify all required server configurations exist"""
        required_server = ["datadir", "urllist", "vpnlist", "database"]
        for key in required_server:
            if key not in config.get("server", {}):
                raise ValueError(f"Missing required server config: {key}")
            # Validate file paths exist (except datadir which we'll create)
            if key != "datadir" and not Path(config["server"][key]).exists():
                raise FileNotFoundError(
                    f"Config path does not exist: {config['server'][key]}"
                )

    def _set_config_defaults(self, config: dict) -> None:
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
                ],  # Default FHD display size for headless browser
                "fullscreen": True,  # Default fullscreen mode
                "daita": "off",  # Default off
            },
        )

    def _initialize_state(self):
        """Initialize all server state variables and data structures"""
        self.accounts = list()  # Available VPN accounts
        self.allocated_accounts = dict()  # Accounts assigned to clients
        self.servers = list()  # Server keys for each VPN/daita combination
        self.done_dict = dict()  # Tracks completed samples per visit combination
        self.pending_visits = set()  # Work still needing completion
        self.url2line = dict()  # Maps URLs to line numbers in urllist
        self.unique_clients = set()  # Tracks connected client IDs
        self.starting_time = time.time()  # Server start timestamp
        self.last_update_time = self.starting_time  # Last data update timestamp

        # Load all required data files and initialize structures
        self._load_urls()  # Load URL list to visit
        self._init_vpn_servers()  # Load VPN servers and create server list
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

    def _get_base_server(self, server_name: str) -> str:
        """Extract base VPN server name (remove _daita suffix if present)"""
        return server_name.removesuffix("_daita")

    def _get_daita_mode(self, server_name: str) -> str:
        """Extract DAITA mode from server name"""
        return "on" if server_name.endswith("_daita") else "off"

    def _load_urls(self) -> None:
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

    def _init_vpn_servers(self) -> None:
        """Load and validate VPN servers from config file, create server list with DAITA variants"""
        with open(self.config["server"]["vpnlist"]) as f:
            base_servers = [line.strip() for line in f if line.strip()]

        try:
            mullvad_servers = requests.get(
                "https://api.mullvad.net/app/v1/relays"
            ).json()["wireguard"]["relays"]
            invalid = [
                v for v in base_servers if not any(s["hostname"] == v for s in mullvad_servers)
            ]

            if invalid:
                print(f"[ERROR] Invalid servers: {', '.join(invalid)}")
                sys.exit(1)
            
            daita_modes = self.config["client"].get("daita", ["off"])
            for vpn_server in base_servers:
                for mode in daita_modes:
                    if mode == "on":
                        self.servers.append(f"{vpn_server}_daita")
                    else:
                        self.servers.append(vpn_server)
            
            print(f"[INIT] Created {len(self.servers)} server configurations from {len(base_servers)} base servers with DAITA modes: {daita_modes}")
                        
        except Exception as e:
            print(f"[ERROR] Failed to validate VPN servers: {e}")
            sys.exit(1)

    def _init_data_directory(self) -> None:
        """Initialize data directory structure"""
        datadir = Path(self.config["server"]["datadir"])

        if not datadir.exists():
            print(f"[INIT] Creating new data directory at {datadir}")
            datadir.mkdir(parents=True)
        else:
            print(f"[INIT] Using existing data directory at {datadir}")

        # Create server directories and URL subdirectories
        for server_name in self.servers:
            server_dir = datadir / server_name

            if not server_dir.exists():
                server_dir.mkdir(parents=True)

            for site_num in self.url2line.values():
                site_dir = server_dir / str(site_num)
                if not site_dir.exists():
                    site_dir.mkdir(parents=True)

    def _load_progress_from_current_data(self) -> None:
        """Load progress from existing directory structure"""
        # Initialize done_dict
        self.done_dict = {
            server_name: {url: 0 for url in self.url2line.keys()}
            for server_name in self.servers
        }

        data_dir = Path(self.config["server"]["datadir"])
        line2url = {v: k for k, v in self.url2line.items()}

        # Scan all server directories
        for server_dir in data_dir.iterdir():
            server_name = server_dir.name
            if server_name not in self.servers:
                print(f"[WARNING] Skipping unknown server directory: {server_name}")
                continue
            # Scan site subdirectories
            for site_dir in server_dir.iterdir():
                site_num = int(site_dir.name)
                url = line2url[site_num]
                # Count PCAP files (samples)
                count = len(list(site_dir.glob("*.pcap")))
                self.done_dict[server_name][url] = count
        print("[INIT] Loaded existing progress from directory structure")

    def _load_accounts(self) -> None:
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

    def _init_visit_list(self) -> None:
        """Initialize pending visits based on current progress"""
        max_samples = self.config["server"].get("samples", 100)

        self.pending_visits = {
            (server_name, url)
            for server_name in self.done_dict
            for url in self.done_dict[server_name]
            if self.done_dict[server_name][url] < max_samples
        }

        print(f"[INIT] Initialized with {len(self.pending_visits)} combinations left")

    def _get_client_config(self):
        """Get client configuration as dictionary"""
        return {
            "visit_count": self.config["server"].get("visits", 10),
            "grace": self.config["timing"].get("grace", 1),
            "min_wait": self.config["timing"].get("min_wait", 2),
            "max_wait": self.config["timing"].get("max_wait", 30),
            "display_size": self.config["server"].get("display_size", [1920, 1080]),
            "fullscreen": self.config["server"].get("fullscreen", True),
            "post_browser_pre_capture_wait": self.config.get(
                "post_browser_pre_capture_wait", 5
            ),
            "post_packet_pre_visit_wait": self.config.get(
                "post_packet_pre_visit_wait", 5
            ),
        }

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
            response = {"account": account}
            response.update(self._get_client_config())
            return jsonify(response)

    def get_status(self):
        """
        Endpoint for server status information

        Returns:
            JSON: Server status including collection progress
        """
        with self.lock:
            # Calculate total collected samples
            total_collected = sum(
                count
                for server_dict in self.done_dict.values()
                for count in server_dict.values()
            )

            return jsonify(
                {
                    "total_to_collect": (
                        self.config["server"].get("samples", 100)
                        * len(self.url2line)
                        * len(self.servers)
                    ),
                    "total_collected": total_collected,
                    "elapsed": time.time() - self.starting_time,
                    "last_update": time.time() - self.last_update_time,
                    "unique_clients": list(self.unique_clients),
                    "allocated_accounts": f"{len(self.allocated_accounts)}/{len(self.accounts) + len(self.allocated_accounts)}",
                }
            )

    def get_vpn_server(self):
        """
        Endpoint to get VPN server assignment
        Returns a VPN server different from current one if possible

        Returns:
            JSON: VPN server, or error message
        """
        client_id = request.args.get("id")
        current_server = request.args.get("server")

        if not client_id or not current_server:
            return jsonify({"error": "Client ID and current server required"}), 400

        current_server = (
            f"{current_server}_daita"
            if request.args.get("daita", "off")
            else current_server
        )

        with self.lock:
            self.unique_clients.add(client_id)
            # Get available servers with pending work
            available_servers = {
                server_name for server_name, url in self.pending_visits
            }

            # Avoid current server if possible, but don't remove if it's the only one available
            if len(available_servers) > 1 and current_server in available_servers:
                available_servers.remove(current_server)

            if not available_servers:
                return jsonify({"error": "No servers available"}), 400

            assigned_server = random.choice(list(available_servers))
            base_server = self._get_base_server(assigned_server)
            daita = self._get_daita_mode(assigned_server)

            return jsonify({"vpn": base_server, "daita": daita})

    def get_work(self):
        """
        Endpoint to get work assignment
        Returns a URL to visit with the specified VPN server

        Returns:
            JSON: Work assignment (URL + VPN + DAITA) or error message
        """
        client_id = request.args.get("id")
        vpn = request.args.get("server")
        daita = request.args.get("daita", "off")

        if not all([client_id, vpn]):
            return jsonify({"error": "Missing required fields"}), 400

        if vpn == "None":
            return jsonify({"error": "None supplied as server - go fetch new"}), 409

        server_name = f"{vpn}_daita" if daita == "on" else vpn

        with self.lock:
            self.unique_clients.add(client_id)

            # Find available work for this server
            available_urls = [
                url for server, url in self.pending_visits if server == server_name
            ]

            if not available_urls:
                return jsonify({"error": "No work available for this server"}), 409

            assigned_url = random.choice(available_urls)

            return jsonify({"url": assigned_url, "vpn": vpn, "daita": daita})

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
            return jsonify({"error": "Missing required fields"}), 400

        try:
            png_data = bytes.fromhex(request.form["png_data"])
            pcap_data = bytes.fromhex(request.form["pcap_data"])
        except Exception as e:
            print(f"[POST] Invalid hex data: {e}")
            return jsonify({"error": "Invalid hex data"}), 400

        client_id = request.form["id"]
        url = request.form["url"]
        vpn = request.form["vpn"]
        daita = request.form["daita"]

        server_name = f"{vpn}_daita" if daita == "on" else vpn

        png_size = len(png_data)
        pcap_size = len(pcap_data)

        print(f"\n[POST] Received work from {client_id}:")
        print(f"  URL: {url}")
        print(f"  Server: {server_name}")
        print(f"  PNG size: {png_size:,} bytes ({png_size / 1024:.1f} KiB)")
        print(f"  PCAP size: {pcap_size:,} bytes ({pcap_size / 1024:.1f} KiB)")

        data_is_valid, msg = self._validate_submitted_data(png_data, pcap_data)
        if not data_is_valid:
            print(f"[POST] Rejected: {msg}")
            return jsonify({"error": msg}), 200

        with self.lock:
            # Check if already done with current combination
            current_count = self.done_dict[server_name][url]
            max_samples = self.config["server"].get("samples", 100)
            if current_count >= max_samples:
                print(
                    f"[POST] Rejected: Already completed {current_count} samples",
                    f"for {url} via {server_name}",
                )
                return jsonify(
                    {"error": f"URL already has maximum of {max_samples} samples"}
                ), 200

            # Determine where to save
            site_num = self.url2line[url]
            base_dir = (
                Path(self.config["server"]["datadir"]) / server_name / str(site_num)
            )

            # Get next sample number
            sample_num = self._get_free_sample_num(server_name, url)
            base_path = base_dir / str(sample_num)

            try:
                base_path.with_suffix(".png").write_bytes(png_data)
                base_path.with_suffix(".pcap").write_bytes(pcap_data)
                base_path.with_suffix(".json").write_text(request.form["metadata"])
                print(f"[POST] Saved sample #{sample_num} to {base_path}")
            except Exception as e:
                print(f"[ERROR] Failed to save files: {e}")
                return jsonify({"error": "Failed to save the data"}), 500

            # Update sample count
            self.done_dict[server_name][url] = current_count + 1

            # Remove from pending if needed
            if self.done_dict[server_name][url] >= max_samples:
                self.pending_visits.discard((server_name, url))
                print(f"[POST] Completed all samples for {url} via {server_name}")

            self.last_update_time = time.time()

        print(f"[POST] Successfully processed sample from {client_id}")
        return jsonify(
            {
                "status": "OK",
                "message": f"Saved sample #{sample_num} for {url} via {server_name}",
            }
        ), 200

    def _validate_submitted_data(self, png_data: bytes, pcap_data: bytes):
        png_size = len(png_data)
        pcap_size = len(pcap_data)

        if pcap_size < self.MIN_PCAP_SIZE or pcap_size > self.MAX_PCAP_SIZE:
            return False, f"PCAP size {pcap_size} out of bounds"

        if png_size < self.MIN_PNG_SIZE:
            return False, f"PNG too small ({png_size} bytes)"

        return True, None

    def _get_free_sample_num(self, server_name: str, url: str):
        """
        Find next available sample number for a server/URL combination

        Args:
            server_name (str): Server name (directory name)
            url (str): URL being sampled

        Returns:
            int: Next available sample number
        """
        dir_path = (
            Path(self.config["server"]["datadir"])
            / server_name
            / str(self.url2line[url])
        )

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

        self.app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    # Create and run server instance
    server = DataCollectionServer()
    server.run()

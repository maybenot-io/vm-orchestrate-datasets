#!/usr/bin/env python3
"""
Data Collection Server for VPN-based web browsing experiments
Handles client coordination, work distribution, and result collection
"""
import sys
import json
import time
import random
import requests
import threading
from pathlib import Path
from flask import Flask, request, jsonify

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
            required_server = ['datadir', 'urllist', 'vpnlist', 'database']
            for key in required_server:
                if key not in config.get('server', {}):
                    raise ValueError(f"Missing required server config: {key}")
                # Validate file paths exist (except datadir which we'll create)
                if key != 'datadir' and not Path(config['server'][key]).exists():
                    raise FileNotFoundError(f"Config path does not exist: {config['server'][key]}")

            # Set default values if not specified
            config.setdefault('server', {
                "samples": 5,               # Default number of samples per URL
                "visits": 10,               # Default number of visits per VPN connection
                "host": "192.168.100.1",    # Default server host
                "port": 5000                # Default server port
            })
            config.setdefault('timing', {
                'grace': 1,         # Additional wait time after page load
                'min_wait': 2,      # Minimum time to spend on each page
                'max_wait': 30      # Maximum time to spend on each page
            })
            config.setdefault('client', {
                'display_size': [1920, 1080],   # Default display size for headless browser
                'fullscreen': True              # Default to fullscreen mode
            })

            return config
        except Exception as e:
            print(f"[ERROR] Config loading failed: {e}")
            sys.exit(1)

    def _initialize_state(self):
        """Initialize all server state variables and data structures"""
        self.accounts = []               # Available VPN accounts
        self.allocated_accounts = {}     # Accounts assigned to clients
        self.vpn_server_list = []        # List of available VPN servers
        self.done_dict = {}              # Tracks completed samples per VPN/URL
        self.pending_visits = []         # Work still needing completion
        self.url2line = {}               # Maps URLs to line numbers in urllist
        self.unique_clients = set()      # Tracks connected client IDs
        self.starting_time = time.time() # Server start timestamp
        self.last_update_time = self.starting_time  # Last data update timestamp

        # Load all required data files and initialize structures
        self._load_vpn_servers()    # Load VPN server list
        self._load_urls()           # Load URL list to visit
        self._init_data_directory()  # Set up data directory structure
        self._load_accounts()       # Load VPN accounts
        self._init_visit_list()     # Initialize work queue

    def _setup_routes(self):
        """Register all Flask route handlers"""
        # Basic info endpoint
        self.app.route('/')(self.hello)

        # Client setup endpoint
        self.app.route('/setup', methods=['GET'])(self.setup_client)

        # Server status endpoint
        self.app.route('/status', methods=['GET'])(self.get_status)

        # VPN server assignment endpoint
        self.app.route('/server', methods=['GET'])(self.get_vpn_server)

        # Work distribution endpoints
        self.app.route('/work', methods=['GET'])(self.get_work)
        self.app.route('/work', methods=['POST'])(self.post_work)

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
            "<li><b>/setup</b> (GET) - Get your VPN account and timing info</li>"
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

    def _load_vpn_servers(self):
        """Load and validate VPN servers from config file"""
        # Read VPN server list from file
        with open(self.config['server']['vpnlist']) as f:
            vpns = [line.strip() for line in f if line.strip()]

        # Validate servers against Mullvad's API
        try:
            mullvad_servers = requests.get("https://api.mullvad.net/app/v1/relays").json()['wireguard']['relays']
            invalid = [v for v in vpns if not any(s['hostname'] == v for s in mullvad_servers)]

            if invalid:
                print(f"[ERROR] Invalid servers: {', '.join(invalid)}")
                sys.exit(1)

            self.vpn_server_list = vpns
            self.done_dict = {vpn: {} for vpn in self.vpn_server_list}
        except Exception as e:
            print(f"[ERROR] Failed to validate VPN servers: {e}")
            sys.exit(1)

    def _load_urls(self):
        """Load and validate URLs from config file"""
        # Read URL list from file
        with open(self.config['server']['urllist']) as f:
            urls = [line.strip() for line in f if line.strip()]

        # Check for duplicates
        if len(urls) != len(set(urls)):
            print("[ERROR] URL list contains duplicates")
            sys.exit(1)

        # Validate URL formats
        for url in urls:
            if not url.startswith(("http://", "https://")):
                print(f"[ERROR] Invalid URL (must be HTTP/HTTPS): {url}")
                sys.exit(1)

        # Create URL to line number mapping
        self.url2line = {url: i for i, url in enumerate(urls)}

        # Initialize completion tracking for each VPN/URL combination
        for vpn in self.vpn_server_list:
            for url in urls:
                self.done_dict[vpn][url] = 0

    def _init_data_directory(self):
        """Initialize data directory structure"""
        datadir = Path(self.config['server']['datadir'])

        if not datadir.exists():
            # Create fresh directory structure
            print(f"[INIT] Creating new data directory at {datadir}")
            datadir.mkdir()

            # Create subdirectories for each VPN server
            for vpn in self.vpn_server_list:
                vpn_dir = datadir / vpn
                vpn_dir.mkdir()

                # Create subdirectories for each URL
                for url in self.url2line:
                    url_dir = vpn_dir / str(self.url2line[url])
                    url_dir.mkdir()
        else:
            print(f"[INIT] Using existing data directory at {datadir}")

    def _load_accounts(self):
        """Load VPN accounts from database file"""
        try:
            with open(self.config['server']['database']) as f:
                self.accounts = json.load(f)["accounts"]

            # Randomize account order for distribution
            random.shuffle(self.accounts)
            print(f"[INIT] Loaded {len(self.accounts)} VPN accounts")
        except Exception as e:
            print(f"[ERROR] Failed to load accounts: {e}")
            sys.exit(1)

    def _init_visit_list(self):
        """Initialize pending visits tracking"""
        self.pending_visits = [
            {"url": url, "vpn": vpn}
            for vpn in self.done_dict
            for url, count in self.done_dict[vpn].items()
            if count < self.config['server'].get('samples', 100)
        ]
        print(f"[INIT] Initialized with {len(self.pending_visits)} pending visits")

    def setup_client(self):
        """
        Endpoint for client setup

        Returns:
            JSON: Account info and client configuration parameters or error message
        """
        id = request.args.get('id')
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

            return jsonify({
                "account": account,
                "visit_count": self.config['server'].get('visits', 10),
                "grace": self.config['timing'].get('grace', 1),
                "min_wait": self.config['timing'].get('min_wait', 2),
                "max_wait": self.config['timing'].get('max_wait', 30),
                "display_size": self.config['server'].get('display_size', [1920,1080]),
                "fullscreen": self.config['server'].get('fullscreen', True)
            })

    def get_status(self):
        """
        Endpoint for server status information

        Returns:
            JSON: Server status including collection progress
        """
        with self.lock:
            # Calculate total collected samples
            total_collected = sum(
                sum(counts.values())
                for counts in self.done_dict.values()
            )

            return jsonify({
                "total_to_collect": self.config['server'].get('samples', 100) * len(self.url2line) * len(self.vpn_server_list),
                "total_collected": total_collected,
                "elapsed": time.time() - self.starting_time,
                "last_update": time.time() - self.last_update_time,
                "unique_clients": list(self.unique_clients),
                "allocated_accounts": f"{len(self.allocated_accounts)}/{len(self.accounts)}"
            })

    def get_vpn_server(self):
        """
        Endpoint to get VPN server assignment
        Returns a VPN server different from the client's current one if possible

        Returns:
            JSON: VPN server hostname or error message
        """
        id = request.args.get('id')
        server = request.args.get('server')
        if not id or not server:
            return "Client ID and current server required", 400

        with self.lock:
            self.unique_clients.add(id)

            # Get available VPN servers with pending work
            available = {v['vpn'] for v in self.pending_visits}

            # Prefer a different server than the current one
            if len(available) > 1 and server in available:
                available.remove(server)

            if not available:
                print(f"[CLIENT] No VPN servers available for {id}")
                return jsonify({"error": "No VPN servers available"}), 400

            assigned_server = random.choice(list(available))
            print(f"[CLIENT] Assigned VPN server {assigned_server} to {id}")
            return jsonify(assigned_server)

    def get_work(self):
        """
        Endpoint to get work assignment
        Returns a URL to visit with the specified VPN server

        Returns:
            JSON: Work assignment (URL + VPN) or error message
        """
        id = request.args.get('id')
        server = request.args.get('server')
        if not id:
            return "Client ID required", 400

        with self.lock:
            self.unique_clients.add(id)
            visits = self.pending_visits

            # Filter by requested VPN server if specified
            if server and server != 'None':
                visits = [v for v in visits if v["vpn"] == server]

            if not visits:
                print(f"[CLIENT] No work available for {id}")
                return jsonify({"error": "No work available"}), 400

            assignment = random.choice(visits)
            print(f"[CLIENT] Assigned work to {id}: {assignment['url']} via {assignment['vpn']}")
            return jsonify(assignment)

    def post_work(self):
        """
        Endpoint to submit work results
        Handles screenshot (PNG), network capture (PCAP), and metadata

        Returns:
            JSON: Success/error status
        """
        required_fields = ['id', 'url', 'vpn', 'png_data', 'pcap_data', 'metadata']
        if any(f not in request.form for f in required_fields):
            print("[POST] Missing required fields in submission")
            return "Missing required fields", 400

        try:
            # Convert hex-encoded data back to bytes
            png_data = bytes.fromhex(request.form['png_data'])
            pcap_data = bytes.fromhex(request.form['pcap_data'])
        except Exception as e:
            print(f"[POST] Invalid hex data: {e}")
            return "Invalid hex data", 400

        # Calculate and log data sizes
        png_size = len(png_data)
        pcap_size = len(pcap_data)
        client_id = request.form['id']
        url = request.form['url']
        vpn = request.form['vpn']

        print(f"\n[POST] Received work from {client_id}:")
        print(f"  URL: {url}")
        print(f"  VPN: {vpn}")
        print(f"  PNG size: {png_size:,} bytes ({png_size/1024:.1f} KiB)")
        print(f"  PCAP size: {pcap_size:,} bytes ({pcap_size/1024:.1f} KiB)")

        # Validate data sizes meet minimum requirements, return 200 OK to avoid
        # triggering client-side errors or retries for invalid data
        if pcap_size < 10*1024 or pcap_size > 2000*1024:
            print(f"[POST] Rejected: PCAP size {pcap_size} out of bounds")
            return "PCAP size invalid", 200
        if png_size < 10*1024:
            print(f"[POST] Rejected: PNG too small ({png_size} bytes)")
            return "PNG size invalid", 200

        with self.lock:
            # Check if we've already collected enough samples for this VPN/URL combo
            if self.done_dict[vpn][url] >= self.config['server'].get('samples', 100):
                print(f"[POST] Rejected: Already completed {self.done_dict[vpn][url]} samples for {url} via {vpn}")
                return "Already completed", 200

            # Determine where to save the files
            site_num = self.url2line[url]
            sample_num = self._get_free_sample_num(vpn, site_num)
            base_path = Path(self.config['server']['datadir']) / vpn / str(site_num) / str(sample_num)

            # Save all three file types (PNG, PCAP, JSON)
            try:
                base_path.with_suffix('.png').write_bytes(png_data)
                base_path.with_suffix('.pcap').write_bytes(pcap_data)
                base_path.with_suffix('.json').write_text(request.form['metadata'])
                print(f"[POST] Saved sample #{sample_num} to {base_path}")
            except Exception as e:
                print(f"[ERROR] Failed to save files: {e}")
                return "Failed to save data", 500

            # Update completion tracking
            self.done_dict[vpn][url] += 1
            visit = {"url": url, "vpn": vpn}

            # Remove from pending if we've hit our sample target
            if self.done_dict[vpn][url] >= self.config['server'].get('samples', 100) and visit in self.pending_visits:
                self.pending_visits.remove(visit)
                print(f"[POST] Completed all samples for {url} via {vpn}")

            self.last_update_time = time.time()

        print(f"[POST] Successfully processed sample from {client_id}")
        return jsonify({
            "status": "OK",
            "message": f"Saved sample #{sample_num} for {url} via {vpn}"
        }), 200

    def _get_free_sample_num(self, vpn, site_num):
        """
        Find next available sample number for a site

        Args:
            vpn (str): VPN server name
            site_num (int): Site line number

        Returns:
            int: Next available sample number
        """
        dir_path = Path(self.config['server']['datadir']) / vpn / str(site_num)

        # Get all existing sample numbers
        existing = [int(f.stem) for f in dir_path.glob('*') if f.stem.isdigit()]

        # Return next available number
        return max(existing) + 1 if existing else 0

    def run(self):
        """
        Start the Flask server
        Uses host and port from configuration
        """
        host = self.config['server'].get('host', '192.168.100.1')
        port = self.config['server'].get('port', 5000)

        print(f"\n[SERVER] Starting on {host}:{port}")
        print(f"[SERVER] Samples per URL: {self.config['server'].get('samples', 100)}")
        print(f"[SERVER] Visits per VPN: {self.config['server'].get('visits', 10)}")
        print(f"[SERVER] Timing - Grace: {self.config['timing'].get('grace', 1)}s")
        print(f"[SERVER] Timing - Min Wait: {self.config['timing'].get('min_wait', 2)}s")
        print(f"[SERVER] Timing - Max Wait: {self.config['timing'].get('max_wait', 30)}s")

        self.app.run(
            host=host,
            port=port,
            debug=False
        )

if __name__ == '__main__':
    # Create and run server instance
    server = DataCollectionServer()
    server.run()
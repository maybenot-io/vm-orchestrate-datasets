#!/usr/bin/env python3
import io
import os
import json
import time
import random
import requests
import tempfile
import subprocess
from PIL import Image
from functools import wraps
from urllib.parse import urljoin
from pyvirtualdisplay import Display
from selenium.webdriver import Firefox
from datetime import datetime, timedelta, timezone
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.wait import WebDriverWait

class DataCollectionClient:
    DEVICE_CONFIG_FILE = "/etc/mullvad-vpn/device.json"

    def __init__(self):
        # Default config and state values
        # some can/will be overriden by server-supplied values
        self.config = {
            'firefox_path': "/usr/lib/mullvad-browser/mullvadbrowser.real",
            'server_url': "http://192.168.100.1:5000",
            # These will be populated by server response
            'grace': None,
            'min_wait': None,
            'max_wait': None,
            'visit_count': None,
            'display_size': None,
            'fullscreen': True
        }
        self.state = {
            'current_server': None,
            'capture_process': None,
            'current_visit_count': 0,
            'session': requests.Session(),
            'identifier': self._generate_identifier()
        }
        print(f"Client ID: {self.state['identifier']}")

    @staticmethod
    def _generate_identifier():
        """Generate client identifier"""
        import uuid
        return str(uuid.uuid4())[:16]

    # VPN Management Methods
    def _run_mullvad_command(self, *args):
        """Run a mullvad CLI command"""
        try:
            result = subprocess.run(["mullvad"] + list(args),
                                   capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Mullvad command error: {e}")
            return None

    def _set_tunnel_state(self, state):
        """Control VPN tunnel connection"""
        result = self._run_mullvad_command(state)
        time.sleep(2)
        return result is not None

    def is_tunnel_active(self):
        """Check if VPN tunnel is active"""
        status = self._run_mullvad_command("status")
        return status and "Connected" in status

    def _configure_vpn(self, account=None):
        """Configure VPN with account setup"""
        try:
            # Basic configuration
            self._run_mullvad_command("lan", "set", "allow")
            self._run_mullvad_command("relay", "set", "tunnel", "wireguard", "-p", "51820")
            self._run_mullvad_command("tunnel", "set", "wireguard", "--daita", "off")

            # Account configuration
            if account:
                self._setup_account(account)
            else:
                print(f"No account received from server")
                return False
            return True
        except Exception as e:
            print(f"VPN configuration error: {e}")
            return False

    def _setup_account(self, account):
        """Set up VPN account credentials"""
        # Stop mullvad daemon to inject json
        self._set_tunnel_state("disconnect")

        timestamp = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        device_config = {
            "logged_in": {
                "account_token": account["account_token"],
                "device": {
                    "id": account["device_id"],
                    "name": account["device_name"],
                    "wg_data": {
                        "private_key": account["device_private_key"],
                        "addresses": {
                            "ipv4_address": account["device_ipv4_address"],
                            "ipv6_address": account["device_ipv6_address"]
                        },
                        "created": timestamp
                    },
                    "hijack_dns": False,
                    "created": timestamp
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_f:
            json.dump(device_config, tmp_f, indent=4)
            tmp_path = tmp_f.name
        subprocess.run(["sudo", "mv", tmp_path, self.DEVICE_CONFIG_FILE], check=True)
        time.sleep(1) # Make sure file has been written
        # start daemon to use new account configuration
        self._set_tunnel_state("connect")
        time.sleep(2)

    # Network Capture Methods
    def _start_pcap_capture(self):
        """Start network traffic capture"""
        self.state['tmp_pcap_file'] = os.path.join(tempfile.gettempdir(), f"{os.urandom(4).hex()}.pcap")
        cmd = [
            "tshark",
            "-i", "any",
            "-f", "port 51820",
            "-s", "64",
            "-w", self.state['tmp_pcap_file']
        ]

        try:
            self.state['capture_process'] = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            time.sleep(0.5)  # Let tshark initialize
        except FileNotFoundError:
            raise RuntimeError("tshark not found - install wireshark")

    def _end_pcap_capture(self) -> bytes:
        proc = self.state.get('capture_process')
        if not proc:
            return b''

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        tmp_file = self.state.get('tmp_pcap_file')
        try:
            with open(tmp_file, 'rb') as f:
                data = f.read()
            os.unlink(tmp_file)
            return data
        except (FileNotFoundError, TypeError):
            return b''

    # Browser Methods
    def _start_browser(self):
        """Launch Mullvad browser instance"""
        try:
            options = Options()
            options.binary_location = self.config['firefox_path']
            service = Service(executable_path="/usr/local/bin/geckodriver")
            return Firefox(options=options, service=service)
        except Exception as e:
            print(f"Browser start error: {e}")
            return None

    @staticmethod
    def _get_performance_metrics(driver):
        """Collect browser performance metrics"""
        return driver.execute_script("""
            const observer = new PerformanceObserver((list) => {
                window.performanceMetrics = window.performanceMetrics || {};
                list.getEntries().forEach(entry => {
                    const type = entry.entryType;
                    window.performanceMetrics[type] = window.performanceMetrics[type] || [];
                    window.performanceMetrics[type].push(entry.toJSON());
                });
            });

            observer.observe({ type: 'navigation', buffered: true });
            observer.observe({ type: 'resource', buffered: true });
            observer.observe({ type: 'paint', buffered: true });
            observer.observe({ type: 'largest-contentful-paint', buffered: true });

            return window.performanceMetrics || {};
        """)

    def _wait_for_page_load(self, driver):
        """
        Wait until document.readyState == 'complete', then stay blocked
        long enough to satisfy min_wait, grace, and max_wait constraints
        from self.config.
        """
        min_wait = self.config.get('min_wait', 0)
        max_wait = self.config.get('max_wait', 30)
        grace = self.config.get('grace', 0)

        if min_wait < 0 or max_wait < 0 or grace < 0:
            raise ValueError("min_wait, max_wait and grace must be non-negative.")
        if min_wait > max_wait:
            raise ValueError("min_wait must not exceed max_wait.")

        start = time.monotonic()

        try:
            WebDriverWait(driver, max_wait).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            # Hard timeout reached, just return
            return

        t_ready = time.monotonic() - start

        target_total = max(min_wait, t_ready + grace)
        target_total = min(target_total, max_wait)

        sleep_needed = target_total - t_ready
        if sleep_needed > 0:
            time.sleep(sleep_needed)

    def _visit_website(self, url):
        display_size = self.config.get('display_size', (1920, 1080))
        display = Display(visible=0, size=display_size)
        display.start()
        """Visit URL and return screenshot + metrics"""
        driver = self._start_browser()
        if not driver:
            display.stop()
            return None, None
        try:
            driver.set_window_size(*display_size)
            driver.maximize_window()
            driver.get(url)
            # Use the config values automatically in wait
            self._wait_for_page_load(driver)

            metrics = self._get_performance_metrics(driver)
            screenshot = self._capture_screenshot(driver)
            return screenshot, metrics
        except Exception as e:
            print(f"Website visit error: {e}")
            return None, None
        finally:
            driver.quit()
            self._close_browser_processes()
            display.stop()

    def _capture_screenshot(self, driver):
        """Capture screenshot"""
        try:
            img = Image.open(io.BytesIO(driver.get_screenshot_as_png()))

            buffer = io.BytesIO()
            img.save(buffer, format="PNG", quality=90)
            return buffer.getvalue()
        except Exception as e:
            print(f"Screenshot error: {e}")
            return None

    def _close_browser_processes(self):
        """Terminate browser processes"""
        try:
            subprocess.run(
                ["pkill", "-f", "mullvad-browser"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            return False

    # Server Communication Methods
    def retry_with_backoff(
        attempts: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: float = 0.3,
        exceptions: tuple = (Exception,)
    ):
        """
        Retry a function with exponential backoff and jitter.

        Parameters:
            attempts   - max retry attempts
            base_delay - starting delay in seconds
            max_delay  - cap for delay between retries
            jitter     - random jitter factor (0.3 means ±30%)
            exceptions - exception types to catch and retry
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                delay = base_delay
                for attempt in range(1, attempts + 1):
                    try:
                        return func(*args, **kwargs)
                    except exceptions as e:
                        if attempt == attempts:
                            raise
                        jittered = delay * random.uniform(1 - jitter, 1 + jitter)
                        sleep_time = min(jittered, max_delay)
                        time.sleep(sleep_time)
                        delay *= 2  # exponential backoff
            return wrapper
        return decorator

    def _server_request(self, endpoint, method="GET", data=None, params=None):
        """Handle server communication"""
        url = urljoin(self.config['server_url'], endpoint)
        if method == "GET":
            response = self.state['session'].get(url, params=params)
        else:
            response = self.state['session'].post(url, data=data)
        response.raise_for_status()
        return response.json() if response.content else {}

    def _setup_client_and_vpn(self):
        """Get VPN account and configuration from server"""
        response = self._server_request("setup", params={'id': self.state['identifier']})
        if not response or not (account := response.get("account")):
            return None

        # Update all state/config values from server response
        self.state['visit_count'] = response.get("visit_count", self.state.get('visit_count'))
        self.config.update({
            'grace': response.get("grace", self.config.get('grace')),
            'min_wait': response.get("min_wait", self.config.get('min_wait')),
            'max_wait': response.get("max_wait", self.config.get('max_wait')),
            'visit_count': response.get("visit_count", self.config.get('visit_count')),
            'display_size': tuple(response.get("display_size", self.config.get('display_size'))),
            'fullscreen': response.get("fullscreen", self.config.get('fullscreen'))
        })
        return account

    def _get_next_task(self):
        """Get next URL to visit from server"""
        params = {
            'id': self.state['identifier'],
            'server': self.state['current_server'] or 'None'
        }
        return self._server_request("work", params=params)

    @retry_with_backoff(attempts=5, base_delay=1, max_delay=30, jitter=0.3, exceptions=(requests.RequestException,))
    def _post_results(self, task, screenshot, pcap, metrics):
        """POST visit results to server"""
        data = {
            'id': self.state['identifier'],
            'url': task['url'],
            'vpn': self.state['current_server'],
            'png_data': screenshot.hex(),
            'pcap_data': pcap.hex(),
            'metadata': json.dumps(metrics)
        }
        return self._server_request("work", method="POST", data=data) is not None

    # Core Workflow Methods
    @retry_with_backoff(attempts=5, base_delay=1, max_delay=30, jitter=0.3, exceptions=(requests.RequestException,))
    def _rotate_vpn_server(self):
        """Request new VPN server from server, supplying current if available"""
        params = {
            'id': self.state['identifier'],
            'server': self.state['current_server'] or 'None'
        }
        response = self._server_request("server", params=params)

        if response:
            print(f"Switching to VPN server: {response}")
            self._run_mullvad_command("relay", "set", "location", response)
            self.state['current_server'] = response
            self.state['current_visit_count'] = 0
            return True

        print("No available VPN servers, waiting...")
        time.sleep(300)
        return False

    def _initialize_vpn(self):
        """Full VPN initialization sequence"""
        try:
            # Disconnect if currently connected
            if self.is_tunnel_active():
                self._set_tunnel_state("disconnect")
            # Get new account configuration and configure client + VPN
            if not (account := self._setup_client_and_vpn()):
                return False
            # Apply configuration
            self._configure_vpn(account)
            # Connect to VPN
            self._set_tunnel_state("connect")
            # Verify connection
            if not self.is_tunnel_active():
                raise RuntimeError("VPN tunnel failed to activate")
            return True
        except Exception as e:
            print(f"VPN initialization error: {e}")
            return False

    def _execute_task(self, task):
        """Process single visit task"""
        print(f"Processing: {task['url']}")

        # Rotate server if needed
        if self.state['current_visit_count'] >= self.config['visit_count'] or not self.state['current_server']:
            if not self._rotate_vpn_server():
                return False

        # Prepare to start packet capture
        time.sleep(3)  # Stabilization period
        self._start_pcap_capture()

        # Visit website
        try:
            start_time = datetime.now().isoformat(sep=' ', timespec='milliseconds')

            screenshot, metrics = self._visit_website(task['url'])
            pcap_data = self._end_pcap_capture()

            end_time = datetime.now().isoformat(sep=' ', timespec='milliseconds')
        except Exception as e:
            print(f"Task execution error: {e}")
            return

        # If metrics, screenshot or pcap failed to populate, skip this task
        if not screenshot or not pcap_data or not metrics:
            print(f"Failed to capture metrics, screenshot or pcap for {task['url']}")
            return False
        # Fetch metrics
        if metrics:
            metrics.update({
                'time_start': start_time,
                'time_end': end_time,
                'vpn_tunnel_visits_since_connect': self.state['current_visit_count'] + 1
            })

        # POST results
        success = self._post_results(task, screenshot, pcap_data, metrics)
        if success:
            self.state['current_visit_count'] += 1

        return success

    def run(self):
        """Main execution loop"""
        while True:
            # Ensure VPN is initialized
            while not self._initialize_vpn():
                time.sleep(random.randint(10, 20))
            # Process tasks
            while True:
                if not (task := self._get_next_task()):
                    time.sleep(random.randint(5, 10))
                    continue

                if not self.is_tunnel_active():
                    self._set_tunnel_state("connect")

                self._execute_task(task)

if __name__ == "__main__":
    client = DataCollectionClient()
    client.run()
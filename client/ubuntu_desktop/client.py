#!/usr/bin/env python3
import io
import json
import os
import random
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urljoin

import requests
from PIL import Image
from pyvirtualdisplay import Display
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import Firefox
from selenium.webdriver.firefox.firefox_profile import FirefoxProfile
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.wait import WebDriverWait


def retry_with_backoff(
    attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.3,
    exceptions: tuple = (Exception,),
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
                        raise e
                    jittered = delay * random.uniform(1 - jitter, 1 + jitter)
                    sleep_time = min(jittered, max_delay)
                    time.sleep(sleep_time)
                    delay *= 2  # exponential backoff

        return wrapper

    return decorator


class DataCollectionClient:
    DEVICE_CONFIG_FILE = "/etc/mullvad-vpn/device.json"

    def __init__(self):
        # Default config and state values
        # some can/will be overriden by server-supplied values
        self.config = {
            "firefox_path": "/usr/lib/mullvad-browser/mullvadbrowser.real",
            "server_url": "http://192.168.100.1:5000",
            "identifier": self._generate_identifier(),
            "session": requests.Session(),
            # These will be populated by server response or by defaults
            "grace": None,
            "min_wait": None,
            "max_wait": None,
            "fullscreen": False,
            "visit_count": None,
            "display_size": None,
            "post_packet_pre_visit_wait": None,
            "post_browser_pre_capture_wait": None,
        }
        self.state = {
            "daita": "off",
            "current_server": None,
            "capture_process": None,
            "current_visit_count": 0,
        }
        print(f"Client ID: {self.config.get('identifier')}")

    @staticmethod
    def _generate_identifier():
        """Generate client identifier"""
        import uuid

        return str(uuid.uuid4())[:16]

    # VPN Management Methods
    def _run_mullvad_command(self, *args):
        """Run a mullvad CLI command"""
        try:
            result = subprocess.run(
                ["mullvad"] + list(args), capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Mullvad command error: {e}")
            return None

    def _run_system_command(self, *args):
        """Run a system command"""
        try:
            result = subprocess.run(
                list(args), capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"system command error: {e}")
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
            self._run_mullvad_command(
                "relay", "set", "tunnel", "wireguard", "-p", "51820"
            )
            self._run_mullvad_command("tunnel", "set", "wireguard", "--daita", "off")

            # Account configuration
            if account:
                self._setup_account(account)
            else:
                print("No account received from server")
                return False

            # Get initial server to use and switch to it, and toggle daita if needed
            params = {
                "id": self.config.get("identifier"),
                "server": "None",
                "daita": self.state.get("daita", "off"),
            }
            response = self._server_request("server", params=params)
            self.state["current_server"] = response.get("vpn")
            self.state["daita"] = response.get("daita")

            self._run_mullvad_command(
                "relay", "set", "location", self.state.get("current_server")
            )
            self._run_mullvad_command(
                "tunnel", "set", "wireguard", "--daita", self.state.get("daita")
            )

            return True
        except Exception as e:
            print(f"VPN configuration error: {e}")
            return False

    def _setup_account(self, account):
        """Set up VPN account credentials"""
        # Stop mullvad daemon to inject json, wait 1s for it to properly stop
        self._run_system_command("sudo", "systemctl", "stop", "mullvad-daemon")
        time.sleep(1)

        timestamp = (datetime.now(timezone.utc) + timedelta(days=365)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
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
                            "ipv6_address": account["device_ipv6_address"],
                        },
                        "created": timestamp,
                    },
                    "hijack_dns": False,
                    "created": timestamp,
                },
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_f:
            json.dump(device_config, tmp_f, indent=4)
            tmp_path = tmp_f.name
        subprocess.run(["sudo", "mv", tmp_path, self.DEVICE_CONFIG_FILE], check=True)
        time.sleep(1)  # Make sure file has been written
        # start daemon to use new account configuration, wait 1s for it to properly start
        self._run_system_command("sudo", "systemctl", "start", "mullvad-daemon")
        time.sleep(1)

    # Network Capture Methods
    def _start_pcap_capture(self):
        """Start network traffic capture"""
        self.state["tmp_pcap_file"] = os.path.join(
            tempfile.gettempdir(), f"{os.urandom(4).hex()}.pcap"
        )
        cmd = [
            "tshark",
            "-i",
            "any",
            "-f",
            "port 51820",
            "-s",
            "64",
            "-w",
            self.state.get("tmp_pcap_file"),
        ]

        try:
            self.state["capture_process"] = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except FileNotFoundError:
            raise RuntimeError("tshark not found - install wireshark")

    def _end_pcap_capture(self) -> bytes:
        proc = self.state.get("capture_process")
        if not proc:
            return b""

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        tmp_file = self.state.get("tmp_pcap_file")
        try:
            with open(tmp_file, "rb") as f:
                data = f.read()
            os.unlink(tmp_file)
            return data
        except (FileNotFoundError, TypeError):
            return b""

    # Browser Methods
    def _start_browser(self):
        """Launch Mullvad browser instance"""
        try:
            options = Options()
            options.binary_location = self.config.get("firefox_path")
            profile = FirefoxProfile()
            profile.set_preference("browser.cache.disk.enable", False)
            profile.set_preference("privacy.clearOnShutdown.cache", True)
            service = Service(executable_path="/usr/local/bin/geckodriver")
            return Firefox(options=options, service=service)
        except Exception as e:
            print(f"Browser start error: {e}")
            return None

    def _prepare_for_visit(self):
        """Prepare for website visit by starting display and browser"""
        try:
            display_size = self.config.get("display_size", (1920, 1080))
            display = Display(visible=0, size=display_size)
            display.start()
            driver = self._start_browser()
            if not driver:
                display.stop()
                return None, None
            driver.set_window_size(*display_size)
            driver.maximize_window()
            return driver, display
        except Exception as e:
            print("Display or browser initialization error:", e)
            return None, None

    def _get_performance_metrics(self, driver):
        driver.execute_script("""
            window.performanceMetrics = {};
            const observer = new PerformanceObserver((list) => {
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
        """)
        # Sleep 1s to ensure the Performance Observer is given time to actually
        # fetch the expected resources, this is due to it being ran asynchronously
        time.sleep(1)
        return driver.execute_script("return window.performanceMetrics || {};")

    def _wait_for_page_load(self, driver):
        """
        Wait until document.readyState == 'complete', then stay blocked
        long enough to satisfy min_wait, grace, and max_wait constraints
        from self.config.
        """
        min_wait = self.config.get("min_wait", 0)
        max_wait = self.config.get("max_wait", 30)
        grace = self.config.get("grace", 0)

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
        print("time ready:", t_ready)
        target_total = max(min_wait, t_ready + grace)
        target_total = min(target_total, max_wait)

        sleep_needed = target_total - t_ready
        print("sleep needed:", sleep_needed)
        if sleep_needed > 0:
            time.sleep(sleep_needed)

    def _visit_website(self, driver, display, url):
        try:
            driver.get(url)
            # Use the config values automatically in wait
            self._wait_for_page_load(driver)

            # Immediately after finishing wait, stop capture process then collect all data
            pcap_data = self._end_pcap_capture()
            metrics = self._get_performance_metrics(driver)
            screenshot = self._capture_screenshot(driver)

            return pcap_data, screenshot, metrics
        except Exception as e:
            print(f"Website visit error: {e}")
            return None, None, None
        finally:
            if driver and display:
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
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    # Server Communication Methods
    def _server_request(self, endpoint, method="GET", data=None, params=None):
        """GET or POST to the server as requested"""
        url = urljoin(self.config.get("server_url"), endpoint)
        try:
            if method == "GET":
                response = self.config.get("session").get(url, params=params)
            else:
                response = self.config.get("session").post(url, data=data)
            # 409 status code: Current server/DAITA combination is finished, need to rotate.
            if response.status_code == 409:
                self._rotate_vpn_server()
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.HTTPError as e:
            print(f"HTTP error for {endpoint}: {e}")
        return None

    def _setup_client_and_get_vpn_account_config(self):
        """Get VPN account and configuration from server"""
        response = self._server_request(
            "setup", params={"id": self.config.get("identifier")}
        )
        if not response or not (account := response.get("account")):
            return None

        # Update all state/config values from server response
        # Safe/sane defaults are used if something isn't supplied.
        self.config.update(
            {
                "grace": response.get("grace", 5),
                "min_wait": response.get("min_wait", 20),
                "max_wait": response.get("max_wait", 30),
                "visit_count": response.get("visit_count", 10),
                "display_size": tuple(response.get("display_size", [1920, 1080])),
                "fullscreen": response.get("fullscreen", True),
                "post_browser_pre_capture_wait": response.get(
                    "post_browser_pre_capture_wait", 5
                ),
                "post_packet_pre_visit_wait": response.get(
                    "post_packet_pre_visit_wait", 5
                ),
            }
        )
        return account

    @retry_with_backoff(
        attempts=5,
        base_delay=1,
        max_delay=30,
        jitter=0.3,
        exceptions=(requests.RequestException,),
    )
    def _get_next_task(self):
        """Get next URL to visit from server"""
        params = {
            "id": self.config.get("identifier"),
            "server": self.state.get("current_server", "None"),
            "daita": self.state.get("daita", "off"),
        }
        return self._server_request("work", params=params)

    @retry_with_backoff(
        attempts=5,
        base_delay=1,
        max_delay=30,
        jitter=0.3,
        exceptions=(requests.RequestException,),
    )
    def _post_results(self, task, screenshot, pcap, metrics):
        """POST visit results to server"""
        data = {
            "id": self.config.get("identifier"),
            "url": task["url"],
            "vpn": self.state.get("current_server", "None"),
            "daita": self.state.get("daita", "off"),
            "png_data": screenshot.hex(),
            "pcap_data": pcap.hex(),
            "metadata": json.dumps(metrics),
        }
        return self._server_request("work", method="POST", data=data) is not None

    # Core Workflow Methods
    @retry_with_backoff(
        attempts=5,
        base_delay=1,
        max_delay=30,
        jitter=0.3,
        exceptions=(requests.RequestException,),
    )
    def _rotate_vpn_server(self):
        """Request new VPN server from server, supplying current if available"""
        # Disconnect from current connection
        self._set_tunnel_state("disconnect")
        params = {
            "id": self.config.get("identifier"),
            "server": self.state.get("current_server", "None"),
            "daita": self.state.get("daita", "off"),
        }
        response = self._server_request("server", params=params)

        if response:
            vpn_server = response.get("vpn")
            daita_mode = response.get("daita")
            print(f"Switching to VPN server: {vpn_server}")
            self._run_mullvad_command("relay", "set", "location", vpn_server)
            self.state["current_server"] = vpn_server

            print(f"Setting DAITA to: {daita_mode.upper()}")
            self._run_mullvad_command(
                "tunnel", "set", "wireguard", "--daita", daita_mode
            )
            self.state["daita"] = daita_mode

            # Connect to new server combination
            self._set_tunnel_state("connect")
            self.state["current_visit_count"] = 0

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
            if not (account := self._setup_client_and_get_vpn_account_config()):
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
        print(
            f"Processing: {task['url']} via {self.state.get('current_server')}, DAITA: {self.state.get('daita')}"
        )

        # Rotate server if needed
        if self.state.get("current_visit_count", 0) >= self.config.get(
            "visit_count", 1
        ) or not self.state.get("current_server", None):
            if not self._rotate_vpn_server():
                return False
        # Prepare browser and display
        driver, display = self._prepare_for_visit()

        # Optional grace period between starting display/browser and starting packet capture
        if (grace := self.config.get("post_browser_pre_capture_wait", 0)) > 0:
            print("post browser sleeping for:", grace)
            time.sleep(grace)
        # Start packet capture
        self._start_pcap_capture()

        # Optional grace period between starting packet capture and visiting website
        if (grace := self.config.get("post_packet_pre_visit_wait", 0)) > 0:
            print("post packet capture start sleeping for:", grace)
            time.sleep(grace)

        # Visit website
        try:
            start_time = datetime.now().isoformat(sep=" ", timespec="milliseconds")

            pcap_data, screenshot, metrics = self._visit_website(driver, display, task["url"])

            end_time = datetime.now().isoformat(sep=" ", timespec="milliseconds")
        except Exception as e:
            print(f"Task execution error: {e}")
            return False
        finally:
            # Increment current visit count regardless of visit success or failure
            # This is always done, since even a 'failed' visit can/will have caused
            # some network traffic in the VPN tunnel, essentially "using up" a visit
            self.state["current_visit_count"] += 1

        # If metrics, screenshot or pcap failed to populate, skip this task
        if not screenshot or not pcap_data or not metrics:
            print(f"Failed to capture metrics, screenshot or pcap for {task['url']}")
            return False
        # Fetch metrics
        if metrics:
            metrics.update(
                {
                    "time_start": start_time,
                    "time_end": end_time,
                    "vpn_tunnel_visits_since_connect": self.state.get(
                        "current_visit_count"
                    ),
                }
            )

        # POST results and return
        return self._post_results(task, screenshot, pcap_data, metrics)

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

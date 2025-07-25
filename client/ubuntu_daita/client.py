#!/usr/bin/env python3
import argparse
import random
from typing import Any
from selenium.webdriver import Firefox
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
import time
import subprocess
import platform
import os
import tempfile
import requests
from urllib.parse import urljoin
import json
from datetime import datetime, timedelta, timezone
from PIL import Image
import io
import psutil
import socket
import hashlib

DEVICE_CONFIG_FILE = r"/etc/mullvad-vpn/device.json"

# global variables to store the process object of the capture
capture_process = None
tmp_pcap_file = os.path.join(tempfile.gettempdir(), f"{os.urandom(6).hex()}.pcap")

# global variables to store identity and states
whoami = None
current_server = None
visit_count = 10
daita_on = False

# global session for all requests through a proxy
session = requests.Session()

def start_pcap_capture(windows_interface="Ethernet0") -> None:
    global capture_process, tmp_pcap_file
    tmp_pcap_file = os.path.join(tempfile.gettempdir(), f"{os.urandom(6).hex()}.pcap")
    cmd = []
    # using tshark to capture network traffic, only UDP packets and only the
    # first 64 bytes of each packet
    if platform.system() == "Windows":
        cmd = ["tshark", "-i", windows_interface, "-f" ,"port 51820" ,"-s", "64", "-w", tmp_pcap_file]
    else: # Linux and potentially macOS
        cmd = ["sudo", "tshark", "-i", "any", "-f" ,"port 51820" ,"-s", "64", "-w", tmp_pcap_file]
    capture_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return

def end_pcap_capture() -> bytes:
    global capture_process, tmp_pcap_file
    capture_process.terminate()
    capture_process.wait()

    cmd = ["sudo", "cat", tmp_pcap_file]
    cat_pcap = subprocess.run(cmd, capture_output=True)
    pcap_data = cat_pcap.stdout

    cmd = ["sudo", "rm", tmp_pcap_file]
    subprocess.run(cmd)
    return pcap_data

def wait_for_page_load(driver, timeout, extra_sleep=2) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script('return document.readyState') == 'complete'
    )
    time.sleep(extra_sleep)

def start_browser(custom_path):
    try:
        options = Options()
        options.binary_location = custom_path
        firefox_service = Service(executable_path="/usr/local/bin/geckodriver",)
        driver = Firefox(options=options, service=firefox_service)
        return driver
    except Exception as error:
        print("exception on start_browser:", error)
        return None

def get_metadata(driver) -> dict:
    metadata = driver.execute_script("""
        window.performanceMetrics = {};
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
    return metadata

def visit_site(driver, url, timeout) -> tuple[bytes | None, dict | None]:
    screenshot_as_binary = None
    try:
        driver.command_executor.set_timeout(timeout)
        driver.get(url)
        wait_for_page_load(driver, timeout)
        metadata = get_metadata(driver)
    except Exception as error:
        print("exception on visit:", error)
        driver.quit()
        close_executable("mullvad-browser")
        return None

    try:
        screenshot_as_binary = driver.get_screenshot_as_png()
        # resize screenshot
        # Load the screenshot into Pillow Image
        image = Image.open(io.BytesIO(screenshot_as_binary))

        # Resize the image to 50% of its original size
        new_size = (int(image.width / 2), int(image.height / 2))
        resized_image = image.resize(new_size, Image.LANCZOS)

        # Save the resized image to a BytesIO object in PNG format with 90% quality
        image_bytes_io = io.BytesIO()
        resized_image.save(image_bytes_io, format="PNG", quality=90)
        screenshot_as_binary = image_bytes_io.getvalue()
    except Exception as error:
        print("exception on screenshot:", error)
    finally:
        driver.quit()
        close_executable("mullvad-browser")

    return screenshot_as_binary, metadata

def close_executable(executable_name) -> bool:
    try:
        subprocess.run(
            ["sudo", "pkill", "-f", executable_name],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except:
        return False

def is_mullvadvpn_service_running() -> bool:
    try:
        result = subprocess.run(["sudo", "systemctl", "is-active", "mullvad-daemon"],
            capture_output=True, text=True, check=True)
        return result.returncode == 0
    except Exception as e:
        print("is_mullvadvpn_service_running error", e)
        return False

def toggle_mullvadvpn_service(action) -> bool:
    try:
        print("Toggling mullvadvpn service:", action)
        if action == "on":
            action = "start"
        else:
            action = "stop"
        subprocess.run(["sudo", "systemctl", action, "mullvad-daemon"], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        return True
    except Exception as e:
        print("toggle_mullvadvpn_service error", e)
        return False

def toggle_mullvadvpn_tunnel(action) -> bool:
    try:
        print("Toggling mullvadvpn tunnel:", action)
        if action == "on":
            action = "connect"
        else:
            action = "disconnect"

        subprocess.run(["mullvad", action], capture_output=True, text=True, check=True)
        time.sleep(2)
        return True
    except Exception as e:
        print("toggle_mullvadvpn_tunnel error", e)
        return False

def is_mullvadvpn_tunnel_running() -> bool:
    try:
        result = subprocess.run(["mullvad", "status"], capture_output=True, text=True, check=True)
        return "Connected" in result.stdout
    except Exception as e:
        print("is_mullvadvpn_tunnel_running error", e)
        return False

def configure_mullvad() -> bool:
    try:
        # enable LAN access
        command = ["mullvad", "lan", "set", "allow"]
        subprocess.run(command, capture_output=True, text=True, check=True)

        # use default mullvad port 51820
        command = ["mullvad", "relay", "set", "tunnel", "wireguard", "-p", "51820"]
        subprocess.run(command, capture_output=True, text=True, check=True)

        # default start with daita off
        command = ["mullvad", "tunnel", "set", "wireguard", "--daita", "off"]
        subprocess.run(command, capture_output=True, text=True, check=True)

        return True
    except Exception as e:
        print("configure_mullvad error", e)
        return False

def configure_mullvad_for_visit(server) -> bool:
    global current_server, daita_on
    try:
        # request new VPN server to use, given by a GET to the /server endpoint
        params = {}
        params['id'] = whoami
        params['server'] = current_server if current_server else 'None'
        params['daita'] = 'on' if daita_on else 'off'
        response = session.get(urljoin(server, "server"), params=params)
        data = response.json()
        vpn_server = data.get("vpn", "*")
        daita_mode = data.get("daita", "*")
        if daita_mode == "*" or vpn_server == "*":
            return False

        if daita_on and daita_mode == 'off':
            # daita currently enabled but should be disabled
            command = ["mullvad", "tunnel", "set", "wireguard", "--daita", daita_mode]
            subprocess.run(command, capture_output=True, text=True, check=True)
            daita_on = False
        elif not daita_on and daita_mode == 'on':
            # daita currently disabled but should be enabled
            command = ["mullvad", "tunnel", "set", "wireguard", "--daita", daita_mode]
            subprocess.run(command, capture_output=True, text=True, check=True)
            daita_on = True
        
        if current_server != vpn_server:
            toggle_mullvadvpn_tunnel("off")
            subprocess.run(["mullvad", "relay", "set", "location", vpn_server],
                           capture_output=True, text=True, check=True)
            current_server = vpn_server
            toggle_mullvadvpn_tunnel("on")

        return is_mullvadvpn_tunnel_running()
    except Exception as e:
        print("configure_mullvad_for_visit error", e)
        return False
    
def toggle_daita(daita: str) -> bool:
    global daita_on
    try:
        command = ["mullvad", "tunnel", "set", "wireguard", "--daita", daita]
        subprocess.run(command, capture_output=True, text=True, check=True)
        daita_on = (daita == "on")
        return True
    except Exception as e:
        print("toggle_daita error", e)
        return False

def get_device_json(account) -> dict[str, dict[str, Any]]:
    # we set the timestamp 1 year in the future, this is to prevent the client
    # from refreshing our keys, the refresh doesn't work very well when we set
    # a custom relay
    timestamp = datetime.now(timezone.utc) + timedelta(days=365)
    timestamp = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
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

def setup_vpn(server) -> bool:
    global session
    try:
        response = session.get(urljoin(server, "setup"), params={'id': whoami})

        if response.status_code != 200:
            print("Received unexpected status code from server:", response.status_code)
            return False

        # we assume the output from the server is correct, and looks something like:
        # {
        #   "account": {
        #     "account_token": "9321816363818742",
        #     "device_id": "a3eedd02-09c1-4f5b-9090-9f3d27ea66bb",
        #     "device_ipv4_address": "10.64.10.49/32",
        #     "device_ipv6_address": "fc00:bbbb:bbbb:bb01::a40:a31/128",
        #     "device_name": "gifted krill",
        #     "device_private_key": "MCWA6YO5PBE/MEsyRqs6Teej1GKqhGJFnH3xCCvjC2c="
        #   }
        # }
        data = response.json()
        account = data["account"]

        # stop the mullvadvpn service and disconnect the tunnel
        if is_mullvadvpn_service_running():
            toggle_mullvadvpn_tunnel("off")
            toggle_mullvadvpn_service("off")

        # overwrite the device config with data submitted by the server
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_f:
            json.dump(get_device_json(account), tmp_f, indent=4)
            tmp_path = tmp_f.name
        subprocess.run(["sudo", "mv", tmp_path, DEVICE_CONFIG_FILE], check=True)

        # reload systemctl since we changed files related to the service
        # and give it a second to successfully restart
        subprocess.run(["sudo", "systemctl", "daemon-reload"])
        time.sleep(1)

        # enable the mullvadvpn daemon again, this has to be done prior to the
        # configuration of the mullvad daemon
        toggle_mullvadvpn_service("on")

        # make some configuration
        configure_mullvad()

        # and finally, enable the tunnel
        toggle_mullvadvpn_tunnel("on")

        if not is_mullvadvpn_tunnel_running():
            raise Exception("unable to establish a mullvad vpn tunnel connection")

        return True
    except Exception as e:
        print("setup failed, error", e)
        return False

def get_work(server) -> dict | None:
    global session, current_server, daita_on
    try:
        params = {}
        params['id'] = whoami
        params['server'] = current_server if current_server else 'None'
        params['daita'] = 'on' if daita_on else 'off'
        response = session.get(urljoin(server, "work"), params=params)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None

def post_work_to_server(server, url, vpn, daita, png_data, pcap_data, metadata) -> bool:
    global session, whoami
    payload = {
        'id': whoami,
        'url': url,
        'vpn': vpn,
        'daita': daita,
        'png_data': png_data.hex(),
        'pcap_data': pcap_data.hex(),
        'metadata': json.dumps(metadata)
    }
    try:
        return session.post(urljoin(server, "work"), data=payload).status_code == 200
    except requests.RequestException:
        return False

def successful_tunnel_restart() -> bool:
    toggle_mullvadvpn_tunnel("off")
    toggle_mullvadvpn_service("off")
    toggle_mullvadvpn_service("on")
    toggle_mullvadvpn_tunnel("on")
    return is_mullvadvpn_tunnel_running()

def generate_identifier() -> str:
    ip_addresses = []

    # Get info about all network interfaces
    for _, interface_addresses in psutil.net_if_addrs().items():
        for address in interface_addresses:
            if address.family == socket.AF_INET:  # Check for IPv4 addresses
                ip_address = address.address
                if ip_address != "127.0.0.1":  # Exclude localhost
                    ip_addresses.append(ip_address)

    # Fallback to localhost if no external IP found
    if not ip_addresses:
        ip_addresses.append('127.0.0.1')

    # Concatenate all IP addresses into a single string
    concatenated_ips = ''.join(ip_addresses)

    # Hash the concatenated string to generate a fixed-length identifier
    hash_object = hashlib.md5(concatenated_ips.encode())
    hex_dig = hash_object.hexdigest()

    # Return the first 16 characters of the hash
    return hex_dig[:16]

def main(args) -> None:
    global whoami, current_server, visit_count, daita_on, session

    # deterministic identifier of 16 characters, derived from the IP addresses
    # of the machine
    whoami = generate_identifier()
    print(f"whoami: {whoami}")

    server = "http://" + args.server if not args.server.startswith("http://") else args.server

    while True:
        while not setup_vpn(server):
            r = random.randint(10, 20)
            print(f"VPN is not setup, sleeping for {r} seconds")
            time.sleep(r)

        # Keep count of how many visits have been performed using current VPN server
        current_visit_count = 0

        while True:
            work = get_work(server)
            if not work:
                # No work means we're out of URLs to visit for this VPN-server
                # at which point, the connection and current_visit_count is reset
                if not configure_mullvad_for_visit(server):
                    # No server available either, indicating done with collection - sleep for 5 minutes to allow check to be ran before resuming
                    time.sleep(300)
                current_visit_count = 0
                r = random.randint(5, 10)
                print(f"No work available for current server, sleeping for {r} seconds then continuing with new server")
                time.sleep(r)
            else:
                if not is_mullvadvpn_service_running():
                    toggle_mullvadvpn_service("on")
                    toggle_mullvadvpn_tunnel("on")
                print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} Got work: {work}")
                driver = start_browser(args.firefox)
                if driver is None:
                    print("Failed to start browser, skipping work")
                    continue
                # If we've reached enough visits for this VPN connection, we ask for a new one.
                if current_visit_count >= visit_count or not current_server:
                    configure_mullvad_for_visit(server)
                    current_visit_count = 0
                # Sleep for 3 seconds to let the browser start up and/or VPN connection settle.
                time.sleep(3)
                time_start = datetime.now().isoformat(sep=' ', timespec='milliseconds')
                start_pcap_capture()
                try:
                    png, metadata = visit_site(driver, work['url'], args.timeout)
                except Exception as e:
                    print("Error during visit_site:", e)
                    end_pcap_capture()
                    continue
                if png is None or metadata is None:
                    print("Failed to visit site, skipping work")
                    end_pcap_capture()
                    continue
                pcap_bytes = end_pcap_capture()
                time_end = datetime.now().isoformat(sep=' ', timespec='milliseconds')
                metadata['time_start'] = time_start
                metadata['time_end'] = time_end
                metadata['num_visit'] = current_visit_count
                print(f"Captured {len(png)/1024:.1f} KiB of png data.")
                print(f"Captured {len(pcap_bytes)/1024:.1f} KiB of pcap data.")
                while not post_work_to_server(server, work["url"], current_server, work['daita'], png, pcap_bytes, metadata):
                    r = random.randint(10, 20)
                    print(f"Failed to post work to server, retrying in {r} seconds")
                    time.sleep(r)
                current_visit_count += 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capture a screenshot with Selenium and send to a server.")
    # Mullvad Browser binary path argument with a default value
    parser.add_argument("--firefox", default="/usr/lib/mullvad-browser/mullvadbrowser.real",
                        help="Path to the Firefox binary.")
    # Timeout argument with a default value of 20 seconds
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="Time to wait for website to load.")
    # Collection server URL argument with a default value
    parser.add_argument("--server", default="http://192.168.100.1:5000",
                        help="URL of the collection server.")
    parser.add_argument("--restart-tunnel-threshold", type=int, default=5,
                        help="Restart tunnel threshold.")
    args = parser.parse_args()
    main(args)

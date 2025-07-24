# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a VM orchestration system for collecting network traffic datasets over VPN tunnels. The system consists of:

- **Host scripts**: Manage VM lifecycle and monitor data collection
- **Server component**: Flask-based coordinator that distributes work to clients  
- **Client component**: Selenium-based web scraper that captures traffic and screenshots
- **Processing utilities**: Data validation, cleanup, and analysis tools

The architecture follows a distributed client-server model where VMs connect to a host-based server to receive URLs to visit while capturing encrypted VPN traffic.

## Development Commands

### Host Setup and VM Management
```bash
# Initial host setup (installs QEMU/KVM, creates network bridge)
./host_scripts/setup.sh

# Create a client VM from ISO
./host_scripts/create_client_vm.sh

# Clone existing VM to create multiple instances  
./host_scripts/clone_vm.sh

# Start/stop all client VMs
./host_scripts/toggle_vms.sh on|off

# Clean up all VMs and associated files
./host_scripts/cleanup_vms.sh
```

### Data Collection Workflow
```bash
# Start server in screen session (example for ubuntu_desktop)
screen -S server
python3 server/ubuntu_desktop/server.py --samples 1000 --datadir ./data --vpnlist ./env/vpnlist.txt --urllist ./env/urllist.txt --visits 10

# Start monitoring in separate screen session
screen -S monitor  
./host_scripts/monitor.sh

# Check data quality and prune bad samples
python3 host_scripts/check.py --dir ./data --vpnlist ./env/vpnlist.txt --prune
```

### Data Processing and Analysis
```bash
# Extract performance metrics from JSON metadata files
python3 processing/qoe.py /path/to/data --out metrics.csv

# Convert raw PCAP files to trace format for analysis
python3 processing/raw2traces.py --dir ./data --results ./traces --classes 10 --samples 100
```

### Server API Endpoints
- `GET /status` - Collection progress (collected/target counts)
- `GET /setup?id=<client_id>` - Client registration and VPN account allocation
- `GET /work?id=<client_id>` - Get next URL to visit
- `POST /work` - Submit collected data (PCAP, PNG, JSON files)

## Architecture Details

### Client-Server Communication
- Server runs on host at `192.168.100.1:5000` (configurable in setup.sh)
- Clients register with unique IDs and receive VPN accounts from a pool
- Work distribution uses pending queue to prevent duplicate assignments
- Automatic restart mechanism when collection targets are met

### Data Collection Process
Each client VM:
1. Connects to Mullvad VPN using allocated account
2. Requests URL assignments from server
3. Visits URLs using Selenium/Firefox while capturing traffic with tshark
4. Takes screenshots to verify successful page loads
5. Uploads PCAP, PNG, and JSON metadata to server

### Monitoring and Quality Control
- `monitor.sh` polls server status and triggers data validation
- `check.py` identifies and removes outlier files based on size thresholds
- Automatic server restart when bad data is detected
- Collection continues until all targets are met with valid data

## File Structure Notes

- `docs/` contains VM configuration details and collection specifics
- `env/` directory should contain `vpnlist.txt` and `urllist.txt` files
- `data/` directory stores collected raw data (PCAP/PNG/JSON triplets)
- `processing/` contains post-collection analysis tools:
  - `qoe.py` - Extracts QoE metrics from JSON metadata files
  - `raw2traces.py` - Converts PCAP files to trace format for ML analysis
- Each collection creates timestamped subdirectories with PCAP/PNG/JSON triplets

## Dependencies

Host requires: qemu-kvm, libvirt-daemon-system, virtinst, python3-flask, python3-requests
Client VMs require: Mullvad VPN, Mullvad Browser, selenium, tshark, PIL
Processing requires: scapy (for PCAP parsing), standard Python libraries

The system is designed for Ubuntu 24.04 clients but the architecture supports other OS types through the modular server/client script organization.
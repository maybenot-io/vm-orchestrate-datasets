# small_kau_daita

A small collection to verify tooling working properly for collecting data using refactored ubuntu_desktop VM/scripts.
The collection was of 50 samples per website per server for a total of 5'000 samples.

All logfiles and environmental files (urllist, vpnlist, config.json) can be found in this directory.

In general, the client VM received a VPN-server to use from the host/server, then performed 10 visits to a randomized URL using that VPN-server before asking for a new, different VPN-server to use. Effectively this meant swapping between DAITA being active or inactive, since only se-got-wg-001 was used.
This continues until there are no more URL:s left to visit for each server.

## Versions of Client/Server scripts used

This collection was performed using client- and server scripts as found at the following repository state:
[35b5f5e71881795e5b5d6edd4a7ae70df5ecd232](https://github.com/maybenot-io/vm-orchestrate-datasets/tree/35b5f5e71881795e5b5d6edd4a7ae70df5ecd232)

Quick links: [client.py](https://github.com/maybenot-io/vm-orchestrate-datasets/blob/35b5f5e71881795e5b5d6edd4a7ae70df5ecd232/client/ubuntu_desktop/client.py) and [server.py](https://github.com/maybenot-io/vm-orchestrate-datasets/blob/35b5f5e71881795e5b5d6edd4a7ae70df5ecd232/server/ubuntu_desktop/server.py)

## URL-list

50 pages from kau.se with varying degrees of elements on the page (text, video, images) - See urllist.txt

## VPN-list

1 server, only se-got-wg-001, both DAITA traffic and 'regular' VPN traffic was captured - See vpnlist.txt

## Collection-log

Timeline of the collection:

- Collection started at Mon Aug 4 11:45:41 PM UTC 2025
    - Minor issue with clients during the night, unsure what exactly caused it, but restarting all client VM:s around 09 AM UTC fixed it 
- Collection ended at Tue Aug 5 10:31:06 AM UTC 2025

## Practical details of the collection

The collection was performed in the following steps:

- Install a base client VM for ubuntu_desktop using `create_client_vm.sh`, wait for it to power off after initial OS installation,
- Clone to 10 separate clients using `clone.sh` host script,
- Start one `screen` session for the server and one for the monitoring script,
- Start the server.py script in the server `screen` while in the root of the repo directory,

```bash
./server/ubuntu_desktop/server.py
```

- Start the monitor.sh script in the monitor `screen` while in the host_script directory, enter `ubuntu_desktop` when prompted,

```bash
./monitor.sh
```

- The scripts should now be set up properly, so all client VM:s are started using host script `toggle_vms.sh on`
- Some common solutions to issues:
    - if the server, check or monitor scripts go crazy, restart them,
    - if the client is acting up either VNC in to it and problemsolve, or reboot the VM, whichever works best for you.

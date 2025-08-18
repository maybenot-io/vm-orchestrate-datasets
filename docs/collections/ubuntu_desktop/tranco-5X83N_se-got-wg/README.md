# small_kau_daita

This collection was of 100 samples per website per server for a total of 100'000 samples.
The 100 samples per website were split up between 5 servers at the same location, effectively balancing the load between them.
This was done to avoid negatively impacting any single server's performance with a large amount of requests, and to avoid getting IP-blocked for visiting the same site(s) too many times in a short span of time.

All logfiles and environmental files (urllist, vpnlist, config.json) can be found in this directory.

In general, the client VM received a VPN-server to use from the host/server, then performed 10 visits to a randomized URL using that VPN-server before asking for a new, different VPN-server to use.
This continues until there are no more URL:s left to visit for each server.

## Versions of Client/Server scripts used

This collection was performed using client- and server scripts as found at the following repository state:
[80c1458bdb02a1f092882a9cc4aa9f29629ffebf](https://github.com/maybenot-io/vm-orchestrate-datasets/tree/80c1458bdb02a1f092882a9cc4aa9f29629ffebf)

Quick links: 
[client.py](https://github.com/maybenot-io/vm-orchestrate-datasets/blob/71829707f655cadefa780022b78d69e6a50b9b70/client/ubuntu_desktop/client.py) and 
[server.py](https://github.com/maybenot-io/vm-orchestrate-datasets/blob/80c1458bdb02a1f092882a9cc4aa9f29629ffebf/server/ubuntu_desktop/server.py)

## URL-list

1000 pages/subpages from some of the top 250 visited websites, picked from the Tranco list with ID 5X83N (12th Aug 2025)  - See urllist.txt

## VPN-list

5 servers, se-got-wg-001 through se-got-wg-005, only 'regular' (non-DAITA) VPN traffic was captured - See vpnlist.txt

## Collection-log

Timeline of the collection:

- Collection started at Fri Aug 15 06:22:06 PM UTC 2025
    - Due to mistakenly using an older version of the monitoring script, the collection was flagged completed on Mon Aug 18 04:12:39 AM UTC 2025, but after updating and restarting at a later point, it continued as expected.
- Collection ended at Mon Aug 18 12:28:09 PM UTC 2025

## Practical details of the collection

The collection was performed in the following steps:

- Install a base client VM for ubuntu_desktop using `create_client_vm.sh`, wait for it to power off after initial OS installation,
- Clone to 25 separate clients using `clone.sh` host script,
- Start one `screen` session for the server and one for the monitoring script,
- Start the server.py script in the server `screen` while in the root of the repo directory,

```bash
./server/ubuntu_desktop/server.py
```

- Start the monitor.sh script in the monitor `screen` while in the host_script directory, enter `ubuntu_desktop` when prompted,

```bash
./monitor.sh
```

- The scripts should now be set up properly, so all client VM:s can be started using host script `toggle_vms.sh on`
- Some common solutions to issues:
    - if the server, check or monitor scripts go crazy, restart them,
    - if the client is acting up either VNC in to it and problemsolve, or reboot the VM, whichever works best for you.
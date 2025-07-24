# small_kau

A smaller collection to verify tooling working properly for collecting data using the ubuntu_desktop VM/scripts.
The collection was of 5 samples per website per server for a total of 1500 samples.

All logfiles and environmental files (urllist, vpnlist) can be found in this directory.

In general, the client VM received a VPN-server to use from the host/server, then performed 10 visits to a randomized URL using that VPN-server before asking for a new, different VPN-server to use.
This continues until there are no more URL:s left to visit for each server.

## Versions of Client/Server scripts used

This collection was performed using the client- and server scripts as found at the following commit ID:
[be1e60493d5d9e41790abc7b069987d05ad9660f](https://github.com/maybenot-io/vm-orchestrate-datasets/commit/be1e60493d5d9e41790abc7b069987d05ad9660f)

## URL-list

50 pages from kau.se with varying degrees of elements on the page (text, video, images) - See urllist.txt

## VPN-list

6 servers, with geograpic spread through Europe as well as one server in the US - See vpnlist.txt

## Collection-log

Timeline of the collection:

- Collection started at Wed Jul 23 05:23:19 PM UTC 2025
    - An issue with the check-script caused the collection to seem like it was finished at Wed Jul 23 09:13:33 PM UTC 2025, but after manual intervention, running the check-script with correct flags, additional samples were needed. 
    - Similar issue happened at Wed Jul 23 10:47:38 PM UTC 2025, but after restarting the monitoring and server scripts it finished with valid data. 
- Collection ended at Wed Jul 23 11:28:40 PM UTC 2025

## Practical details of the collection

The collection was performed in the following steps:

- Install a client VM for ubuntu_desktop and start it up
- Start one `screen` session for the server and one for the monitoring script
- Start the server.py script in the server `screen` while in the root of the repo directory

```bash
./server.py  --datadir ./data/ --urllist env/kaulist.txt --vpnlist ./env/vpnlist.txt --database ./env/database.json --samples 5
```

- Start the monitor.sh script in the monitor `screen` while in the host_script directory, enter `ubuntu_desktop` when prompted

```bash
./monitor.sh
```

- The scripts should now be set up properly and the collection will begin, at which point it's just a matter of waiting until it is complete.
- Mileage may vary slightly, here are some common solutions to issues:
    - if the server, check or monitor scripts go crazy, restart them,
    - if the client is acting up either VNC in to it and problemsolve, or reboot the VM, whichever works best for you.

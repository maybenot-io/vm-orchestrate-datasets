# Ubuntu Desktop VM

The below described VM and scripts are used to collect web traffic over Mullvad VPN without using DAITA, their anti-measure for traffic analysis.

## Background and VM setup

This VM is based on Ubuntu 24.04.1 (Noble Numbat); the following configurations and alterations have been made to the ISO:

- The grub.cfg file has its boot records edited to only have one entry, marked as autoinstall (to skip the graphical setup) with a basic user-data file including an used named `ubuntu` with the password `password`. The user-data and meta-data files are placed in /autoinstaller/ and can be found in this directory. This is the menuentry used in grub.cfg:
```bash
menuentry "Ubuntu Autoinstaller" {
    set gfxpayload=keep
    linux /casper/vmlinuz autoinstall ds=nocloud\;s=/autoinstaller/ ---
 initrd /casper/initrd.gz
}
```
- bootstrap.service is added to automatically run the script `first_boot.sh` after the general installation finishes (using late-commands):
```bash
late-commands:
    - curtin in-target -- systemctl daemon-reexec
    - curtin in-target -- chmod +x /usr/local/bin/first_boot.sh
    - curtin in-target -- systemctl enable bootstrap.service
```
- The `first_boot.sh` script installs all necessary packages on first reboot, such as: Mullvad VPN, Mullvad Browser, GeckoDriver, tShark, Python-Selenium. See the script found in this directory for an exhaustive list.
- After all packages have been installed, this repository is cloned, and the corresponding client script within (./client/ubuntu_desktop/client.py) is set as executable.
- An autostart login.desktop entry and script is created to make sure the client script it started on each reboot **AFTER the graphical environment is ready** (important for getting selenium to run a non-headless browser), and from there the collection can begin.

## Data Collected using this VM and script combination

One PCAP/PNG/JSON file is saved of each website visit made:

- The PCAP contains the encrypted website traffic data,
- The PNG is used to verify the visit was valid (not blocked by CAPTHCHA or an offline website),
- The JSON file contains captured metadata of the visit (QoE data, timestamp, visit sequence number).

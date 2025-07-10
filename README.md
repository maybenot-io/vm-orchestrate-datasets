# vm-orchestrate-datasets

A toolset to automate the orchestration of virtual machines for collecting datasets of network activities over VPN-tunnels.

## ⚙️ Installation & Setup

* Running the `setup.sh` script on the host will set up necessary packages (qemu-kvm/virsh/libvirt) and prepare the environment (enable libvirtd, create virsh network bridge) for running the server-script.

* Prepare an ISO file (or an already installed virtual machine) to pull down this repository and run the client-script you want to use on each restart.
    - All necessary configurations needed/performed for each VM we have used can be found in the docs folder.

* The script `create_client_vms.sh` can be used to create a VM from your prepared ISO, and the script `clone.sh` can be used to create multiple clones of your already prepared fully installed VM.
    - These can be used in tandem (`create_client_vms.sh` to install the VM, then `clone.sh` to create more instances of that VM)

## 📢 Usage

Each server/client script combination is currently tailored to a specific type of collection, please refer to the docs of each VM to learn more about their specific usage.
The VMs themselves more or less only need to be pre-configured to pull down this repository on first boot and execute the desired `client.py` script, how this can be achieved will be described in more detail in each relevant documentation of the VMs.

Generally how to perform data collection using these scripts, after performing the installation and setup as above:

* Set up two `screen` sessions on the host, one will run `server.py`, and one `monitor.py`,
* Start the `server.py` script with all necessary flags in its `screen` session, then start the client-VMs (can be done using the host script `toggle_vms.sh`),
* Wait until the collection is finished. The monitoring-script will pause the collection as it nears completion and perform data validation.
* The monitoring script does the following:
    - If all of the collected data is valid, the `screen` sessions are terminated and the VM:s are turned off.
    - If there was any invalid data, it is pruned and `server.py` is restarted.
    - The remaining valid data is always saved and used to calculate how much is remaining on restart of `server.py`.


## 🛠 Compatibility

Currently Ubuntu 24.04 is the only OS we are using as client-machines, and the scripts are tailored as such.
This might change in the future, though, to include more operating systems and/or flavours of GNU/Linux.

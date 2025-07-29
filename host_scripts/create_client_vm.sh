#!/bin/bash
# This script creates a base VM for client machines using virt-install.

set -e

VM_CPU=2
VM_RAM=4096
VM_DISK=20
VM_NETWORK="clientnetwork"
BASE_VM_NAME="client-base"
BASE_DISK_PATH="/var/lib/libvirt/images/${BASE_VM_NAME}.qcow2"

echo_msg() { echo -e "\033[1;33m$1\033[0m"; }
error_exit() { echo -e "\033[1;31m[-] $1\033[0m"; exit 1; }

if ! sudo virsh dominfo "$BASE_VM_NAME" &>/dev/null; then
    read -p "[?] Enter the path to the OS image (e.g., /var/lib/libvirt/images/ubuntu.iso): " OS_IMAGE

    [ ! -f "$OS_IMAGE" ] && error_exit "OS image not found at $OS_IMAGE"

    echo_msg "[+] Creating base VM: $BASE_VM_NAME"
    sudo qemu-img create -f qcow2 "$BASE_DISK_PATH" "${VM_DISK}G"

    sudo virt-install --name "$BASE_VM_NAME" \
        --vcpus "$VM_CPU" \
        --memory "$VM_RAM" \
        --disk path="$BASE_DISK_PATH",format=qcow2,bus=virtio \
        --cdrom "$OS_IMAGE" \
        --network network="$VM_NETWORK",model=virtio \
        --os-variant ubuntu24.04 \
        --video qxl,ram=65536,vram=65536 \
        --graphics vnc,listen=0.0.0.0 \
        --console pty,target_type=serial \
        --noautoconsole > /dev/null 2>&1

    echo "[*] Started install of base VM $VM_NAME - installation will probably take 10-15 minutes to finish"
    echo "[*] The VM is finished installing when it shows as shut off in the output of 'sudo virsh list --all'"
    echo "[*] After the base VM is finished installing, you can run the clone script to create more copies of it"
fi
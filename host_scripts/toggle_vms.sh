#!/bin/bash
# This script toggles all VM:s on or off (excluding the base VM).

VMS=($(sudo virsh list --all --name | grep '[0-9]'))

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 {on|off}"
    exit 1
fi

case "$1" in
    on)
        for VM in "${VMS[@]}"; do
            state=$(sudo virsh domstate "$VM")
            if [[ "$state" == "running" ]]; then
                echo "$VM is already running. Skipping..."
                continue
            fi
            echo "Starting $VM..."
            sudo virsh start "$VM"
        done
        ;;
    off)
        for VM in "${VMS[@]}"; do
            state=$(sudo virsh domstate "$VM")
            if [[ "$state" == "shut off" ]]; then
                echo "$VM is already shut down. Skipping..."
                continue
            fi
            echo "Shutting down $VM..."
            sudo virsh shutdown "$VM"
        done
        ;;
    *)
        echo "Invalid option. Use 'on' or 'off'."
        exit 1
        ;;
esac
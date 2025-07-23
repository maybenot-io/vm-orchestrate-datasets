#!/bin/bash
# Collection monitoring script with verification after restart

status_url="http://192.168.100.1:5000/status"
check_every=300

script_dir=$(dirname "$(readlink -f "$0")")
root_dir="$(realpath "$script_dir/..")"

# Paths
read -p "[*] Enter the name of the VM you're using (which server script): " VM_NAME

server_script="$root_dir/server/$VM_NAME/server.py"
check_script="$script_dir/check.py"
datadir="$root_dir/data"
vpnlist="$root_dir/env/vpnlist.txt"
log_file="$root_dir/collectionlog.txt"

if [ ! -r "$server_script" ]; then
    echo "!! Error: Server script not found or not readable: $server_script"
    exit 1
fi

function check_status {
    curl -s "$status_url" | jq -r '[.total_collected,.total_to_collect] | @tsv'
}

function verify_completion {
    # 5 seconds sleep for server to start properly and be able to respond
    echo "-> Verifying collection status..." >> "$log_file"
    sleep 5

    read -r collected target <<< $(check_status)
    echo "$(date) - After restart: $collected/$target" >> "$log_file"

    if [ "$collected" -eq "$target" ]; then
        return 0
    else
        return 1
    fi
}

function run_check_and_restart {
    echo "-> Running data validation..." >> "$log_file"

    # Graceful server shutdown (CTRL+C)
    screen -S server -X stuff $'\003'
    sleep 5

    # Prune bad files if any are found
    if ! python3 "$check_script" --dir "$datadir" --vpnlist "$vpnlist" > /dev/null 2>&1; then
        echo "!! Check script found bad files! Running again with --prune..." >> "$log_file"
        python3 "$check_script" --prune --dir "$datadir" --vpnlist "$vpnlist" > /dev/null 2>&1
        
    fi

    # Restart server using its previous command
    screen -S server -X stuff '!!\n'
    echo "-> Server restarted" >> "$log_file"
}

while true; do
    read -r collected target <<< $(check_status)

    if [ -z "$collected" ] || [ -z "$target" ]; then
        echo "$(date) - Could not get status" >> "$log_file"
        sleep $check_every
        continue
    fi

    echo "$(date) - Progress: $collected/$target" >> "$log_file"

    if [ "$collected" -eq "$target" ]; then
        run_check_and_restart

        if verify_completion; then
            echo "-> Collection verified complete!" >> "$log_file"
            # Shut off clients using toggle_vms.sh script
            "$script_dir/toggle_vms.sh" off
            # Kill server screen session
            screen -S server -X quit
            exit 0
        else
            # If verification fails, server continues to run after restart
            # and the clients will continue their collection of data
            echo "-> More data needed after check" >> "$log_file"
        fi
    fi

    sleep $check_every
done
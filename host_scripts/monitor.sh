#!/bin/bash
# Collection monitoring script with verification after restart

log_file="collection.log"
status_url="http://192.168.100.1:5000/status"
check_every=300

script_dir=$(dirname "$(readlink -f "$0")")

function check_status {
    curl -s "$status_url" | jq -r '.total_collected,.total_to_collect'
}

function verify_completion {
    echo "-> Verifying collection status..." >> "$log_file"
    sleep 15

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

    # Graceful server shutdown
    screen -S server -X stuff $'\003'
    sleep 5

    # Run data check
    if ! python3 "$script_dir/check.py" data --prune; then
        echo "!! Check script failed!" >> "$log_file"
    fi

    # Restart server
    screen -S server -X stuff "cd \"$script_dir\" && python3 server.py\n"
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
            exit 0
        else
            echo "-> More data needed after check" >> "$log_file"
        fi
    fi

    sleep $check_every
done
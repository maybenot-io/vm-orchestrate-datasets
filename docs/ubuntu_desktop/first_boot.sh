#!/bin/bash

LOG_FILE="/var/log/client-setup.log"
MAX_RETRIES=5
RETRY_DELAY=10

log() {
    echo "$(date) - $1" >> $LOG_FILE
}

log "Client setup starting.."

check_internet_connection() {
    ping -c 4 8.8.8.8 &>/dev/null
    return $?
}

log "Making sure we have an internet connection to begin with.."
retries=0
while ! check_internet_connection; do
    retries=$((retries + 1))
    if [ $retries -ge $MAX_RETRIES ]; then
        log "Internet connection unavailable after $MAX_RETRIES retries"
        exit 1
    fi
    log "Internet Connection unavailable, retrying in $RETRY_DELAY seconds.."
    sleep $RETRY_DELAY
done
log "Internet Connection established"

log "Making sure curl and git is installed.."
if ! sudo apt install curl git -y; then
    log "Error installing curl"
    exit 1
fi

log "Installing GeckoDriver.."
if ! wget https://github.com/mozilla/geckodriver/releases/download/v0.35.0/geckodriver-v0.35.0-linux64.tar.gz; then
    log "Error downloading GeckoDriver"
    exit 1
fi

if ! tar -xvzf geckodriver-v0.35.0-linux64.tar.gz --remove-files; then
    log "Error extracting GeckoDriver"
    exit 1
fi

if ! mv geckodriver /usr/local/bin; then
    log "Error moving GeckoDriver to /usr/local/bin"
    exit 1
fi
log "Successfully installed GeckoDriver"

log "Installing tShark.."
echo "wireshark-common wireshark-common/install-setuid boolean true" | sudo debconf-set-selections
if ! DEBIAN_FRONTEND=noninteractive sudo apt install -y tshark; then
    log "Error installing tShark"
    exit 1
fi
log "Successfully installed tShark"

log "Adding ubuntu user to wireshark group to allow non-root packet capture.."
if ! sudo usermod -aG wireshark ubuntu; then
    log "Error adding user to wireshark group"
    exit 1
fi

log "Installing Mullvad VPN and Mullvad Browser.."
if ! curl -fsSLo /usr/share/keyrings/mullvad-keyring.asc https://repository.mullvad.net/deb/mullvad-keyring.asc; then
    log "Error downloading Mullvad keyring"
    exit 1
fi

if ! echo "deb [signed-by=/usr/share/keyrings/mullvad-keyring.asc arch=$(dpkg --print-architecture)] https://repository.mullvad.net/deb/stable $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/mullvad.list; then
    log "Error adding Mullvad repository"
    exit 1
fi

if ! apt update; then
    log "Error updating apt repositories"
    exit 1
fi
if ! apt install mullvad-vpn -y; then
    log "Error installing Mullvad VPN"
    exit 1
fi
log "Successfully installed Mullvad VPN"

if ! apt install mullvad-browser -y; then
    log "Error installing Mullvad Browser"
    exit 1
fi
log "Successfully installed Mullvad Browser"

if ! apt install python3-selenium python3-flask python3-psutil python3-requests -y; then
    log "Error installing Python3 packages"
    exit 1
fi
log "Installed necessary Python3 packages"

log "Fetching the client script.."
if ! git clone --quiet https://github.com/maybenot-io/vm-orchestrate-datasets /home/ubuntu/Documents/vm-orchestrate-datasets/; then
    log "Error cloning the github repository"
    exit 1
fi

log "Changing permissions on directory.."
if ! sudo chown -R ubuntu:ubuntu /home/ubuntu/Documents/vm-orchestrate-datasets/; then
    log "Error changing permissions on directory"
    exit 1
fi

if ! sudo chmod +x /home/ubuntu/Documents/vm-orchestrate-datasets/client/ubuntu_desktop/client.py; then
    log "Error making client.py executable"
    exit 1
fi

log "Making it so that client.py starts on every reboot.."

sudo cat << EOF > /home/ubuntu/login.sh
#!/bin/bash
/usr/bin/python3 /home/ubuntu/Documents/vm-orchestrate-datasets/client/ubuntu_desktop/client.py
EOF

sudo chmod +x /home/ubuntu/login.sh && sudo chown ubuntu:ubuntu /home/ubuntu/login.sh

if ! [ -e /home/ubuntu/login.sh ]; then
    log "Error creating the login script file"
    exit 1
fi

sudo mkdir -p /home/ubuntu/.config/autostart
sudo cat << EOF > /home/ubuntu/.config/autostart/login.desktop
[Desktop Entry]
Type=Application
Exec=/home/ubuntu/login.sh
Name=StartPyclient
Comment=Starts the client.py script
X-GNOME-Autostart-enabled=true
EOF

if ! [ -e /home/ubuntu/.config/autostart/login.desktop ]; then
    log "Error creating the .desktop file in .config/autostart"
fi

if ! sudo chown -R ubuntu:ubuntu /home/ubuntu/.config; then
    log "Error changing permissions on .config directory to ubuntu:ubuntu"
    exit 1
fi

log "Setup complete, ready to start fetching work - starting the client on next reboot!"

sudo systemctl reboot
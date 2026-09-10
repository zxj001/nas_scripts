
# Minecraft Bedrock Server setup
https://pimylifeup.com/ubuntu-minecraft-bedrock-server/

## Requirements
// Ubuntu 20+ or Debian
sudo apt install curl wget unzip grep screen openssl -y
// Create a user specifically to run the Minecraft server
sudo useradd -m mcserver
// Add yourself to the group
sudo usermod -aG mcserver $USER
sudo mkdir -p /home/mcserver/minecraft_bedrock
// You may need to make the home directory accessible to the group
sudo chmod 774 /home/mcserver

## Download and extract the server
// Get the download URL for Minecraft Bedrock
DONWLOAD_URL=$(curl -H "Acccept-Encoding: identity" -H "Accept-Language: en" -s -L -A "Mozilla/4.0 (compatible; MSIE 6.0 Windows NT 5.1; BEDROCK_UPDATER)" https://minecraft.net/en-us/download/server/bedrock | grep -o 'https.*/bin-linux/.*.zip')
// Download the Minecraft server 
sudo wget -U "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1; BEDROCK-UPDATER)" $DOWNLOAD_URL -O /home/mcserver/minecraft_bedrock/bedrock-server.zip
sudo unzip /home/mcserver/minecraft_bedrock/bedrock-server.zip -d /home/mcserver/minecraft_bedrock/
sudo rm /home/mcserver/minecraft_bedrock/bedrock-server.zip
// Give mcserver user permission
sudo chown -R mcserver: /home/mcserver/

## Configure the Server
sudo nano /home/mcserver/minecraft_bedrock/server.properties

## Starting the Server
cd /home/mcserver/minecraft_bedrock/
sudo LD_LIBRARY_PATH=. ./bedrock_server

## Starting the server at boot
sudo nano /home/mcserver/minecraft_bedrock/start_server.sh

### Bash Script
```
#!/usr/bin/env bash

SERVER_PATH=/home/mcserver/minecraft_bedrock/

/usr/bin/screen -dmS mcbedrock /bin/bash -c "LD_LIBRARY_PATH=$SERVER_PATH ${SERVER_PATH}bedrock_server"
/usr/bin/screen -rD mcbedrock -X multiuser on
/usr/bin/screen -rD mcbedrock -X acladd root
```
sudo chmod +x /home/mcserver/minecraft_bedrock/start_server.sh

## Stop the server script

sudo nano /home/mcserver/minecraft_bedrock/stop_server.sh
```
#!/usr/bin/env bash

/usr/bin/screen -Rd mcbedrock -X stuff "stop \r"
```
sudo chmod +x /home/mcserver/minecraft_bedrock/stop_server.sh
sudo chown -R mcserver: /home/mcserver/

## Creating Minecraft service script
sudo nano /etc/systemd/system/mcbedrock.service
```
[Unit]
Description=Minecraft Bedrock Server
Wants=network-online.target
After=network-online.target

[Service]
Type=forking
User=mcserver
Group=mcserver
ExecStart=/usr/bin/bash /home/mcserver/minecraft_bedrock/start_server.sh
ExecStop=/usr/bin/bash /home/mcserver/minecraft_bedrock/stop_server.sh
WorkingDirectory=/home/mcserver/minecraft_bedrock/
Restart=always
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
```
sudo systemctl enable mcbedrock
sudo systemctl start mcbedrock
sudo systemctl stop mcbedrock

## Accessing Server Commandline
sudo screen -r mcserver/mcbedrock

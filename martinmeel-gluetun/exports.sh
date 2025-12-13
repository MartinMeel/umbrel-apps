#!/bin/bash

# Export Gluetun container name for other apps to reference
export APP_GLUETUN_CONTAINER_NAME="gluetun_server_1"

# Export VPN status endpoint
export APP_GLUETUN_API_URL="http://gluetun_server_1:8688"

# Network mode for other containers to use
export APP_GLUETUN_NETWORK_MODE="container:gluetun_server_1"

export WINDSCRIBE_USERNAME="efdqk1p2-97zvbkq"
export WINDSCRIBE_PASSWORD="d2fw9tengj"
export WINDSCRIBE_REGION="Netherlands"
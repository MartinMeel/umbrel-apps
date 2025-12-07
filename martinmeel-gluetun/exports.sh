#!/usr/bin/env bash

###############################################################################
# Windscribe VPN credentials
# These variables are used by Gluetun inside docker-compose.yml
###############################################################################

# Your Windscribe login (NOT the OpenVPN config credentials)
export WINDSCRIBE_USER="????"
export WINDSCRIBE_PASSWORD="????"


###############################################################################
# OPTIONAL: REGION / CITY SELECTION
#
# You can restrict which Windscribe VPN locations Gluetun connects to.
# If you leave all of these commented out, Gluetun will select the best
# available region on its own.
#
# IMPORTANT:
# - Use only ONE of these at a time (Region OR City).
# - Names must match Gluetun's internal location database.
###############################################################################

# -------------------------
# ▶ Example: Pick a REGION
# -------------------------
# This forces all connections to use *any* server within the selected region.
# Available examples: Netherlands, Germany, Switzerland, France, UK, etc.
#
export SERVER_REGIONS="Netherlands"


# -------------------------
# ▶ Example: Pick a CITY
# -------------------------
# Forces Gluetun to connect only to servers in a specific city.
# You can provide multiple cities comma-separated.
# Examples: Amsterdam, Frankfurt, Zurich, London, Paris
#
export SERVER_CITIES="Amsterdam"


# -------------------------
# ▶ Multiple Cities (Example)
# -------------------------
# Connect to Amsterdam OR Rotterdam OR Frankfurt:
#
# export SERVER_CITIES="Amsterdam,Rotterdam,Frankfurt"
#
###############################################################################

# End of exports.sh

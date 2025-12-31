export SPOTWEB_DB_PASSWORD=$(derive_entropy "spotweb-db-password" | head -c32)
echo "DB Password: $SPOTWEB_DB_PASSWORD"
export MYSQL_ROOT_PASSWORD=$(derive_entropy "spotweb-mysql-root" | head -c32)
echo "Root Password: $MYSQL_ROOT_PASSWORD"

# Remove unwanted file after installation
# This runs during the installation process
if [ -f "/home/umbrel/umbrel/app-data/martinmeel-spotweb/spotwebdbsettings.inc.php" ]; then
    echo "Removing unwanted file..."
    rm -f "/home/umbrel/umbrel/app-data/martinmeel-spotweb/spotwebdbsettings.inc.php"
fi

# Or if the file is in the app's data directory:
#if [ -f "${APP_DATA_DIR}/unwanted-file" ]; then
#    echo "Removing ${APP_DATA_DIR}/unwanted-file..."
#    rm -f "${APP_DATA_DIR}/unwanted-file"
#fi

# Or if it's in a specific container's volume:
#if [ -f "${APP_DATA_DIR}/container-name/unwanted-file" ]; then
#    echo "Removing unwanted file from container volume..."
#    rm -f "${APP_DATA_DIR}/container-name/unwanted-file"
#fi
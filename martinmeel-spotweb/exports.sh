export SPOTWEB_DB_PASSWORD=$(derive_entropy "spotweb-db-password" | head -c32)
echo "DB Password: $SPOTWEB_DB_PASSWORD"
export MYSQL_ROOT_PASSWORD=$(derive_entropy "spotweb-mysql-root" | head -c32)
echo "Root Password: $MYSQL_ROOT_PASSWORD"
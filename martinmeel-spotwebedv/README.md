# This app is based on the dockerfile and info from Erik de Vries. 
See https://github.com/edv/docker-spotweb/ for more information.


### Spotweb retrieves and sends data via the gleutun container for privacy and security reasons.


### Getting started
After installing the app on your umbrel do the following:
- Change the APP_HOST like mentioned above.
- Restart the app from the umbrel screen
- Login to the app via: http://umbrel.local:8085/
- Configure Settings and Change preferences
- To retrieve your spots run this command via your umbrel (ssh into your umbrel)

  ```
  sudo docker exec -it martinmeel-spotwebedv_spotweb_1 php /app/retrieve.php --force
  ```
  Now patiently wait ;-) (Depending on how fast your system is and how many spots you want to retrieve it can take up day's to have everything retrieved!!
  
  
### Extra checks
To check if your network traffic routes through the gluetun container run:

```
sudo docker exec martinmeel-spotwebedv_spotweb_1 wget -qO- https://ifconfig.me
sudo docker exec martinmeel-gluetun_server_1 wget -qO- http://127.0.0.1:80 | head
```


## Have a look at the rest of the documentation for more fine tuned configuration.


### Advanced database configuration

Spotweb requires a database to work. Out of the box this project supports MySQL, PostgreSQL and SQLite. Provide one or more of the following environment variables to configure the database:

- **DB_ENGINE**, one of:
  - `pdo_mysql` (MySQL, default)
  - `pdo_pgsql` (PostgreSQL)
  - `pdo_sqlite` (SQLite)
- **DB_HOST** (default = `mysql`)
- **DB_PORT** (default = `3306`)
- **DB_NAME** (default = `spotweb`)
- **DB_USER** (default = `spotweb`)
- **DB_PASS** (default = `spotweb`)


### Configure Spotweb

- Visit `http://localhost:8085`
- Login with username `admin` and default password `spotweb`
- Configure usenet server, spot retention, etc. and wait for spots to appear (retrieval script by default runs once every 5 minutes, see below how to change this update interval)

### Change Spotweb update interval

- By default Spotweb will update every 5 minutes
- Change the `CRON_INTERVAL` environment variable to any valid cronjob expression (see e.g. https://cron.help/ for more information, default 5 minute interval is configured in the docker-compose.yml file as an example)
- Restart the Spotweb Docker container (during start-up it will display the current configured update interval)

### Store cache outside container

- By default Spotweb store cache (like images) inside of the Docker container (in `/app/cache`)
- This results in the cache being removed when the container is recreated (e.g. when a new Docker image is pulled)
- To retain this cache you can mount a volume to `/app/cache` see the commented lines in the included Docker Compose files on how to do this

### Change timezone

- Change the `TZ` environment variable to any valid timezone (e.g. Europe/Amsterdam or Europe/Lisbon)
- Restart the Spotweb Docker container

### Redirect access log

By default, access logs are sent to stdout. In case you want to change it:

* Set the `ACCESS_LOG` environment variable to the location of your liking (e.g. `/var/log/nginx/access.log`)

* Restart the Spotweb Docker container

### Tip: Using `ownsettings.php`

You can override Spotweb settings by using a custom `ownsettings.php` file. In most cases there is no need to use this feature, so only use this when you know what you are doing!

The example below will mount `/external/ownsettings.php` to `/app/ownsettings.php` inside the container. Spotweb will see the ownsettings file and load it automatically.

```
volumes:
- /external/ownsettings.php:/app/ownsettings.php
```

### Use Spotweb as newznab provider (indexer)

If you want to use Spotweb with for example Sonarr or Radarr (or any tool that is compatible with newznab indexers), create a new (non admin) user in Spotweb and use the API key associated with this new user.

Next step is to set-up a custom newznab indexer in Sonarr or Radarr and point it to the Spotweb url with the API key from the newly created user.

### All environment variables

- **DB_ENGINE**, one of:
  - `pdo_mysql` (MySQL, default)
  - `pdo_pgsql` (PostgreSQL)
  - `pdo_sqlite` (SQLite)
- **DB_HOST** (default = `mysql`)
- **DB_PORT** (default = `3306`)
- **DB_NAME** (default = `spotweb`)
- **DB_USER** (default = `spotweb`)
- **DB_PASS** (default = `spotweb`)
- **TZ** (default = `Europe/Amsterdam`)
- **CRON_INTERVAL** (default = `*/5 * * * *`)
- **ACCESS_LOG** (default = `/dev/stdout`)

### Additional information

- Spotweb is configured as an open system after running docker compose up, so everyone who can access the site can register an account (keep this in mind, and also make sure to change the admin password if you plan to expose Spotweb to the outside world!)
- See the [official Spotweb Wiki](https://github.com/spotweb/spotweb/wiki) for any questions regarding Spotweb

# Umbrel custom-hooks manager

Umbrel custom-hooks manager installs and maintains host-side custom hook files on UmbrelOS v1.7.x.

It is designed for fresh UmbrelOS installs where `/home/umbrel/umbrel/custom-hooks` may not exist yet.

## What it does

- creates `/home/umbrel/umbrel/custom-hooks` if missing
- installs a managed `pre-start` hook and keeps it in place
- creates `/home/umbrel/umbrel/app-data/.smbcredentials` if missing
- keeps `.smbcredentials` at mode `600`
- mounts these SMB shares to Umbrel Downloads:
  - `//192.168.2.168/Films` -> `/home/umbrel/umbrel/home/Downloads/Films`
  - `//192.168.2.168/Films2` -> `/home/umbrel/umbrel/home/Downloads/Films2`
  - `//192.168.2.168/TVSeries` -> `/home/umbrel/umbrel/home/Downloads/TVSeries`
  - `//192.168.2.168/TVSeriesOLD` -> `/home/umbrel/umbrel/home/Downloads/TVSeriesOLD`
- installs `mc` automatically if it is missing
- installs a daily `06:00` Gluetun restart timer
- installs and starts `gluetun-umbreld-watch.service` for manual Gluetun restarts
- provides a small web UI to edit `.smbcredentials`
- provides a live install/runtime log in the app UI

## Gluetun handling

Two restart flows are supported.

### Daily scheduled restart

Handled by:
- `/home/umbrel/umbrel/custom-hooks/gluetun-daily-restart.sh`
- `umbrel-gluetun-daily-restart.timer`

### Manual Gluetun restart

Handled by:
- `/usr/local/bin/gluetun-umbreld-watch.sh`
- `gluetun-umbreld-watch.service`

This service watches Docker events for `martinmeel-gluetun_server_1`, stops dependent Umbrel apps when Gluetun stops, and starts them again after Gluetun comes back healthy.

## First-time setup

1. Install the app from your Umbrel app store.
2. Open the app UI.
3. Replace `CHANGE_ME` values in `.smbcredentials`.
4. Save the file.
5. Wait a few seconds while the credentials watcher reruns the managed `pre-start`.
6. The SMB shares should mount automatically.

## Important paths

- credentials:
  `/home/umbrel/umbrel/app-data/.smbcredentials`
- custom hooks:
  `/home/umbrel/umbrel/custom-hooks`
- app log:
  `/home/umbrel/umbrel/app-data/martinmeel-hook-manager/logs/hook-manager.log`

## Verification

Check mounts:

```bash
mount | grep 192.168.2.168
```

Check daily timer:

```bash
systemctl status umbrel-gluetun-daily-restart.timer --no-pager
```

Check manual watch service:

```bash
systemctl status gluetun-umbreld-watch.service --no-pager
```

Check app log:

```bash
tail -n 100 /home/umbrel/umbrel/app-data/martinmeel-hook-manager/logs/hook-manager.log
```

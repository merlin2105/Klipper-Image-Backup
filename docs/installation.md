# Installation

The installer currently targets Raspberry Pi 4 running Debian-based Raspberry Pi OS or MainsailOS. MainsailOS is accepted only when Debian's version marker and the `apt-get`/`dpkg-query` tools are present. It prints detected hardware, OS, boot mounts, partition layout, and service/frontend presence before asking for confirmation. It installs only the read-only diagnostic service.

```sh
git clone https://github.com/merlin2105/Klipper-Image-Backup.git
cd Klipper-Image-Backup
sudo ./install.sh
```

The installer does not edit boot files, EEPROM settings, partitions, Klipper, Moonraker, Mainsail, or Fluidd. It creates a `kibm` system user, a Python virtual environment under `/opt/klipper-image-backup-manager`, and the `kibm.service` systemd unit. The read-only dashboard does not require a separate password. Re-running the installer removes the obsolete KIBM-only password file from earlier versions.

After installation, open `http://<printer-ip>:8765` from a browser on the trusted local network. This alpha uses HTTP; do not port-forward it to the internet. To remove it, run `sudo ./uninstall.sh`. Deinstallation removes only KIBM files and the KIBM service user marker/account; it never restores boot configuration because this version has not changed any.

The current dashboard and API are unauthenticated and intended only for a trusted local network. Do not expose port 8765 to the internet.

## Installer acceptance criteria

The user should only need to obtain the repository, run `sudo ./install.sh`, and confirm the installation. The installer is responsible for prerequisites, service account and file permissions, Python environment, systemd setup, service startup, and an HTTP readiness check. It must report success only after `http://127.0.0.1:8765/api/system` returns HTTP 200. No follow-up shell commands should be required for a normal fresh install or an upgrade over a previous KIBM install.

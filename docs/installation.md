# Installation

The installer currently targets Raspberry Pi 4 running Debian-based Raspberry Pi OS or MainsailOS. MainsailOS is accepted only when Debian's version marker and the `apt-get`/`dpkg-query` tools are present. It prints detected hardware, OS, boot mounts, partition layout, and service/frontend presence before asking for confirmation. It installs required packages, creates a local HTTPS certificate, configures PAM validation for the invoking Pi account, grants the unprivileged web service read-only access to `/dev/mmcblk0`, installs both services, and checks readiness.

```sh
git clone https://github.com/merlin2105/Klipper-Image-Backup.git
cd Klipper-Image-Backup
sudo ./install.sh
```

The installer does not edit boot files, EEPROM settings, partitions, Klipper, Moonraker, Mainsail, or Fluidd. It creates a `kibm` system user, a Python virtual environment under `/opt/klipper-image-backup-manager`, and the `kibm.service` and `kibm-auth.service` systemd units. Authentication uses the invoking user's existing Linux password; KIBM neither creates a password nor stores the password. Re-running the installer retains the TLS certificate unless the printer's LAN IP changes.

After installation, open `https://<printer-ip>:8765` from a browser on the trusted local network and confirm the locally generated certificate once. The full raw microSD image is streamed directly to the browser; use the browser's normal save dialog to select a PC folder. Do not port-forward the service to the internet. To remove it, run `sudo ./uninstall.sh`. Deinstallation removes KIBM files, its units, local certificate, and the KIBM service account; it never changes printer or boot configuration.

The dashboard diagnosis is available on the LAN. Image creation and download require a session authenticated with the existing Pi account password. Do not expose port 8765 to the internet.

## Installer acceptance criteria

The user should only need to obtain the repository, run `sudo ./install.sh`, and confirm the installation. The installer is responsible for prerequisites, service account and file permissions, Python environment, PAM, local TLS, systemd setup, service startup, and an HTTPS readiness check. It must report success only after `https://127.0.0.1:8765/api/system` returns HTTP 200. No follow-up shell commands should be required for a normal fresh install or an upgrade over a previous KIBM install.

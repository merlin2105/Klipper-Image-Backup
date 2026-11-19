# Installation

The installer currently targets Raspberry Pi 4 running Debian-based Raspberry Pi OS or MainsailOS. MainsailOS is accepted only when Debian's version marker and the `apt-get`/`dpkg-query` tools are present. It prints detected hardware, OS, boot mounts, partition layout, and service/frontend presence before asking for confirmation. It installs only the read-only diagnostic service.

```sh
git clone https://github.com/merlin2105/Klipper-Image-Backup.git
cd Klipper-Image-Backup
sudo ./install.sh
```

The installer does not edit boot files, EEPROM settings, partitions, Klipper, Moonraker, Mainsail, or Fluidd. It creates a `kibm` system user, a root-only environment file for the login credentials, a Python virtual environment under `/opt/klipper-image-backup-manager`, and the `kibm.service` systemd unit. Re-running it reuses its service account and keeps the existing password.

After installation, open `http://<printer-ip>:8765` from a browser on the trusted local network. This alpha uses HTTP; do not port-forward it to the internet. To remove it, run `sudo ./uninstall.sh`. Deinstallation removes only KIBM files and the KIBM service user marker/account; it never restores boot configuration because this version has not changed any.

The installer has not been run on a Raspberry Pi in this development session. Pi hardware installation and network accessibility remain unverified.

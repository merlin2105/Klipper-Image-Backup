# Klipper Image Backup Manager (KIBM)

[Deutsch](README.md) · [English](README.en.md)

KIBM creates full images of a Klipper system microSD card and displays key Raspberry Pi diagnostics. The dashboard runs on the Pi and is available over HTTPS on your local network.

> **Important:** KIBM is in beta. An image of a running system card is not a point-in-time snapshot: files may change while they are being read. USB backups format the explicitly selected drive as exFAT and erase all data already stored on it.

## Contents

- [Requirements](#requirements)
- [Install on a Raspberry Pi](#install-on-a-raspberry-pi)
- [First visit and sign-in](#first-visit-and-sign-in)
- [Features](#features)
- [Safety and backup notes](#safety-and-backup-notes)
- [Updates](#updates)
- [Troubleshooting and administration](#troubleshooting-and-administration)
- [Development setup](#development-setup)
- [Project status and planned features](#project-status-and-planned-features)
- [License](#license)

## Requirements

- Raspberry Pi 4.
- Debian or a Debian-based operating system. MainsailOS is supported on Raspberry Pi 4.
- A regular Linux account with `sudo` privileges. Run the installer with `sudo` from that account.
- Internet access during installation so the installer can download required packages and Python dependencies. The web updater also needs outbound HTTPS access to GitHub.
- For a USB backup: a USB drive connected directly to the Pi, with enough free space.
- For a NAS backup: an SMB share (Synology, TrueNAS, Windows and others) with a user who may write there; ideally the Pi is connected by cable.

KIBM does not replace or reinstall Fluidd, Mainsail, Klipper, or Moonraker. KIBM queries Moonraker locally when checking print status.

## Install on a Raspberry Pi

These commands download the GitHub repository and run its installer. The installer currently supports Raspberry Pi 4 and Debian-based systems.

```sh
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/merlin2105/Klipper-Image-Backup.git
cd Klipper-Image-Backup
sudo ./install.sh
```

Review the detected hardware, operating system, and storage information. Confirm the installation by entering `j`. The installer configures required packages, a restricted KIBM service account, sign-in through your existing Linux account, a local HTTPS certificate, and KIBM system services. It then checks that the services are ready.

On success, the installer prints an address such as `https://<pi-ip-address>:8765/`. Use the exact address shown in the installer output. Port 8765 is intended for access on a trusted local network; do not set up public port forwarding.

## First visit and sign-in

1. Open the HTTPS address printed by the installer on a device on the same network.
2. On the first visit, accept the local certificate in your browser. The installer creates it for the Pi; it is not automatically validated by a public certificate authority.
3. Diagnostics are visible on the dashboard. Backups and image deletion use the existing Linux account that ran the installer when authentication is required.
4. If prompted, enter that account’s current Linux password. KIBM does not change or store it.

Use the flags in the top-right corner to switch between German and English. KIBM remembers your choice in that browser.

## Features

### System diagnostics

The dashboard displays the detected host, operating system, kernel, and architecture, along with CPU, memory, and temperature readings. It lists system drives and partitions with their filesystem, size, and mount point. This diagnostics view reads system information; it does not provide a way to change Klipper configuration.

KIBM uses Moonraker status to detect an active or paused print. If Moonraker is unavailable, KIBM cannot automatically check whether a print is running. In that case, an explicit confirmation is required before a backup.

KIBM also checks whether the system microSD reports errors. It searches the kernel messages since the last boot for read and timeout errors of the card and reads the error counter of the ext4 file systems, which survives reboots. If it finds any, the status bar turns yellow and shows “System microSD reports errors”; the latest kernel message appears as a tooltip. A backup is still possible. What to do next is described under [The system microSD reports read errors](#the-system-microsd-reports-read-errors).

### 1. Save a full image to your PC

1. Select **PC · Full image**.
2. Confirm that no print is running or will start during the backup.
3. Sign in with the existing Pi/Linux account if prompted.
4. Choose where your browser should save the `.img` file.

KIBM reads the system microSD up to the end of its last partition and streams the uncompressed image to the browser. The image is therefore never larger than the used card layout and fits onto a card of the same size. It does not first store the image on the Pi’s system card. The browser saves it to the selected PC location. Transfer progress and the SHA-256 checksum are shown after completion. This PC workflow does not shrink the image and cannot repair the filesystem inside it afterwards, because it flows straight to the browser. For an image that boots reliably, the USB backup is the better choice.

### 2. Back up a full image to a USB drive

1. Connect the USB drive to the Raspberry Pi and select **USB drive · Backup**.
2. Choose the detected drive using the displayed device information.
3. Verify that it is the correct drive and explicitly confirm formatting.
4. Confirm the safe printer status and sign in if prompted.
5. Start the backup and wait for it to finish.

**Formatting as exFAT erases all existing data on the selected USB drive.** KIBM rechecks the drive identity and available capacity before writing. You can optionally have KIBM read the image back after writing and verify it with SHA-256. Because the card is read while the system runs, KIBM briefly freezes the root filesystem before reading, so every change saved up to then has been written from the ext4 journal to the card. KIBM then completes the ext4 journal inside the image and repairs the filesystem with `e2fsck`; the checksum shown belongs to the repaired image. Only then does the file get its `.img` name. An aborted backup leaves no file that looks like a valid image.

The **Shrink the full backup with PiShrink** option creates a smaller copy on the same USB drive. During processing, there must be enough room for a full working copy and the original. If **Keep the full image too** is unchecked, KIBM removes the full intermediate image only after shrinking succeeds. If shrinking fails, the full image is kept. A card written with the shrunk image grows its root partition to the whole card once on first boot and reboots one extra time. When verification is enabled, KIBM reads the written image again.

After completion, you can download the image through your browser. Then use **Safely eject USB drive** before unplugging it.

### 3. Back up a full image to a NAS (SMB)

On many printers the Raspberry Pi and its display are built into the case, and a USB drive is hard to connect. For these setups KIBM writes the image directly to a folder on a network share: Synology, TrueNAS, QNAP, a Windows share or another SMB server (SMB 2 or newer).

#### What you need

- A share on the NAS, for example `Backup`, and a NAS user with **write access** to it. Guest access is not supported.
- Enough free space: at least the card image size, about twice that with PiShrink, because shrinking needs a working copy. The size is shown in the dashboard under **System microSD image**.
- Ideally a **network cable** between the Pi and your network. In testing a Pi 4 wrote 78 MB/s to the NAS over cable and only 4.6 MB/s over Wi-Fi. A 15 GB image takes a few minutes over cable and almost an hour over Wi-Fi.

#### One-time setup

1. Choose **NAS (SMB) · Backup** and sign in with your Pi account.
2. **Search network for NAS** lists devices to click. Or enter the IP address (e.g. `192.168.1.100`) or name of the NAS manually. A pasted address such as `\\192.168.1.100\Backup`, `//192.168.1.100/Backup` or `smb://192.168.1.100/Backup` is split into NAS and share automatically.
3. Enter **user** and **password** of the NAS account. For a domain account, write `DOMAIN\user` in the user field.
4. **Show shares** lists the NAS shares to click. If that does not work, type the **share name** yourself (see [troubleshooting](#nas-shares-are-not-listed)). The name must match exactly, including upper/lower case: `Backup` is not `backup`.
5. **Connect**: KIBM mounts the share briefly and shows its folders. You do not type the folder; you click your way to it. The path line takes you back, **New folder** creates one.
6. **Use this folder** checks that KIBM can write there and saves the connection.

#### Backing up

Selecting the NAS card checks the connection live and shows **✓ Connection OK · //NAS/share/folder** with the free space, or a red ✗ with the reason. The options and **Create image** only appear once the tick is green.

Options and process match the USB backup: flush the root journal, read the card, optionally verify with SHA-256, repair the filesystem in the image with `e2fsck`, optionally shrink with PiShrink. Nothing is formatted on the NAS. File names match USB, for example `PRUSA-20261122-101500-sdkarte.img`, with shrinking `…-sdkarte-geshrinkt.img` and, if kept, `…-sdkarte-voll.img`.

**The folder looks empty during the backup.** KIBM first writes a hidden file `.…-sdkarte.img.part` and renames it to `….img` only after verification and repair, so an aborted run never leaves a half-written file that looks like a valid image. Samba servers such as TrueNAS hide files starting with a dot by default. In Windows Explorer use *View → Show → Hidden items*, in Linux file managers press `Ctrl+H`.

#### Changing and removing

Below the status line are **Change credentials** and **Remove connection**. If you leave the password field empty when changing, KIBM keeps the saved password, but only while NAS, share and user stay the same. A different NAS needs the password again. **Remove connection** only deletes the saved credentials on the Pi; images on the NAS are kept.

#### How KIBM treats the NAS

- The share is mounted only for one action (check, list folders, back up, list images, delete, shrink) and unmounted afterwards. KIBM never changes `/etc/fstab`, so a switched-off NAS never delays booting.
- The password is stored only in `/var/lib/kibm-media/nas-credentials`, readable by root only. It never appears in process lists, logs or browser responses.
- On the NAS, KIBM deletes only the image file you explicitly selected, or its own hidden `.part` file after an abort.
- KIBM lists existing NAS images only when the page loads (signed in), after a NAS backup or via **Reload NAS images**, never in the background, so the NAS can sleep.

### 4. Inspect, shrink, or delete an existing image

When KIBM finds `.img` files on a detected USB drive or in the NAS backup folder, **Existing images** appears. NAS images are only listed when signed in, and only when the page loads, after a NAS backup, or via **Reload NAS images**, so the NAS can sleep. KIBM checks the actual image contents, regardless of the filename. It reads partition and filesystem information and displays found images as selectable tiles.

- **Shrink:** PiShrink is offered only when the filesystem can be checked safely and at least 256 MB of potential savings are estimated. KIBM creates a new copy in the same location and keeps the original.
- **Already compact or not safe to inspect:** KIBM will not offer another shrink.
- **Delete:** Removes only the selected `.img` file. A custom confirmation dialog and, when needed, Pi account sign-in are required. Deletion cannot be undone.

### Cancelling a backup

A running USB or NAS backup and the shrinking of an image can be stopped with **Cancel job** below the progress bar. KIBM stops in every phase, including while e2fsck or PiShrink run, and deletes the incomplete image file or working copy. Older backups stay untouched. If you cancel while the image is being shrunk, the finished and verified full image is kept.

### Progress on the printer

During a USB or NAS backup and while shrinking, KIBM also reports progress to Klipper (the text is German), for example `KIBM: 35 % gesichert (10,6 von 29,8 GB)`:

- **Printer display and Mainsail/Fluidd:** status line via `SET_DISPLAY_TEXT`, updated at most every 10 seconds and cleared one minute after the job ends.
- **KlipperScreen:** a short banner at every step and every 10 %. This needs `[respond]` in the Klipper configuration; without it KIBM sends only the status line. These messages also appear in the Mainsail/Fluidd console.

If Moonraker cannot be reached, the backup continues normally without the printer display. Saving to the PC through the browser shows nothing on the printer.

### GitHub updates

The dashboard checks GitHub for a newer KIBM version and displays the installed and available version numbers. Use **Check for updates** to run the check manually. An available update is installed only after you confirm it.

During installation, KIBM shows a blocking progress dialog. Keep the Raspberry Pi powered on and do not start a backup until the update has finished. KIBM then reports success and offers **Reload page**. Safely eject any connected USB drive before updating; an active backup job also blocks the update.

Updates come only from signed release tags (for example `v0.3.0`), never from the current state of `main`. The updater downloads the confirmed release, verifies its SSH signature against the release key stored during installation, and only then runs the KIBM installer. An unsigned tag or one signed by another key is not installed. The Pi needs outbound HTTPS access to GitHub. See [Creating a release](docs/release.md) for how to publish one. You can investigate an interrupted or failed update with the administration commands below.

Before updating, the installer snapshots the last installed KIBM version and its service configuration. If the new version fails its readiness check, KIBM automatically restores the previous files and Python environment, then checks that version again. The update is still reported as failed; the snapshot does not contain USB images or other user data.

## Safety and backup notes

- **System card:** KIBM reads the source device `/dev/mmcblk0`; the normal backup workflow does not write to the running system microSD.
- **Running Pi:** KIBM captures the image while the Pi stays on. Files and system state can change during the read, so a successful image is not proof of a consistent snapshot or a successful restore.
- **Print status:** A print reported as active or paused by Moonraker blocks the backup. Without Moonraker, automatic print monitoring is unavailable; continue only if you are sure no print is running or about to start.
- **USB target:** Only the explicitly selected drive is formatted as exFAT for a USB backup. This is destructive and erases existing data on that drive.
- **NAS credentials:** The NAS password is stored only in `/var/lib/kibm-media/nas-credentials` (readable by root only) and is never sent back to the browser. The share is mounted only while an action runs; on the NAS, KIBM deletes only the explicitly selected `.img` file.
- **Password:** KIBM checks the existing Linux account through PAM. It does not store or change the password. The web interface uses local HTTPS and CSRF-protected actions.
- **Permissions:** The web process runs unprivileged. Authentication and media operations run through separate local services. The media worker accepts fixed operations rather than arbitrary shell commands.
- **Network:** Do not expose port 8765 to the public internet. Use KIBM on a trusted local network.

More details are available in [Architecture](docs/architecture.md) and the [Security model](docs/security.md).

## Updates

After installation, check for and install updates from the KIBM dashboard. For manual maintenance over SSH, KIBM provides a restricted administration command:

```sh
sudo kibm-admin status
sudo kibm-admin logs
sudo kibm-admin usb
sudo kibm-admin restart
sudo kibm-admin update
```

`status`, `logs`, and `usb` show service status or diagnostic information. `restart` restarts KIBM services. `update` installs the newest signed release from GitHub. The wrapper does not accept arbitrary shell commands.

## Troubleshooting and administration

### Dashboard is unreachable

- Check that you are using the HTTPS address and IP printed by the installer.
- Accept the local certificate on the first visit.
- Check services with `sudo kibm-admin status` and logs with `sudo kibm-admin logs`.
- Make sure port 8765 is reachable on your local network and is not blocked by a firewall.

### GitHub update is not detected or will not install

- Check that the Pi can make outbound HTTPS connections to GitHub.
- Click **Check for updates** to refresh the status.
- Safely eject any connected USB drive in KIBM and wait for backup and download jobs to finish.
- Check `sudo kibm-admin status` and `sudo kibm-admin logs`.

### USB drive is missing or rejected

- Check the USB connection and click **Check prerequisites again**.
- `sudo kibm-admin usb` lists drives detected by the media worker.
- Use a drive with enough space: a regular USB image workflow needs at least the card image size plus overhead. Shrinking requires room for two full image copies plus overhead while it runs.
- Back up existing data elsewhere before starting a USB backup. KIBM formats the selected drive before writing.

### NAS shares are not listed

**Show shares** asks the NAS for its shares using the `smbclient` program. That can fail for several reasons. The backup itself never needs the list: type the share name yourself and click **Connect**.

- **Message “The share list is not available on this system (smbclient is missing)”:** `smbclient` is not installed. The installer sets it up but treats it as optional, so an update never fails because of it. The most common reason is an operating system that is out of support, for example Debian 11 (bullseye, the base of older MainsailOS releases). The Debian security server then no longer serves the newest Samba build (`404 Not Found`) while the package list still points to it. From KIBM 0.2.10b10 the installer then tries the matching build from the regular Debian archive. To do it by hand:

  ```bash
  sudo apt-get update
  sudo apt-get install -t bullseye smbclient
  ```

  Replace `bullseye` with your Debian release name (`grep VERSION_CODENAME /etc/os-release`). Then click **Show shares** again; KIBM does not need a restart.
- **“Sign-in rejected”:** user or password is wrong, or the account may not sign in over SMB. On TrueNAS the user needs *Samba Authentication* enabled; if the user was created before the SMB service, setting the password once more often helps. Some NAS models refuse the share list to accounts without admin rights, while mounting a known share still works.
- **The share you want is missing from the list:** shares marked as not browsable on the NAS (TrueNAS *Browsable to Network Clients* off, on Synology the option to hide the folder from network browsing) and hidden shares ending in `$` are not shown. Entered manually with the correct name, they work normally.
- **“NAS not reachable” or timeout:** check the IP address, that the NAS is on and in the same network. A firewall must allow TCP port 445 from the Pi.

### NAS backup: other problems

- **“Sign-in rejected” when connecting:** first check the exact spelling of the share name, including upper/lower case. Then user, password and the user's rights on the share (on TrueNAS the dataset ACL).
- **“Share not found”:** no share with this exact name exists; usually a typo or wrong upper/lower case.
- **“The NAS name could not be resolved”:** enter the IP address instead of the name, ideally after giving the NAS a fixed address in your router.
- **“No write permission in the backup folder”:** the NAS user may not write there. Adjust the rights on the NAS or choose another folder.
- **Search network for NAS finds nothing:** the search uses mDNS/Bonjour (only when `avahi-utils` is installed) and checks which devices in the Pi's own /24 network accept TCP port 445. A NAS in another network or behind a firewall is not found; enter its IP address manually.
- **The backup is very slow:** check whether the Pi uses Wi-Fi instead of cable (`ip -br addr` shows whether `eth0` has an address). With an active cable connection the Pi prefers it automatically.
- **The folder is empty during the backup:** expected, see above. The hidden `.part` file becomes the `.img` file at the end.

### The system microSD reports read errors

If a backup stops with “The system microSD reports a read error at … GB” or the status bar shows “System microSD reports errors”, the card is very likely failing. Cheap or old cards often fail this way after years of continuous use. Typical side effects: SSH disconnects immediately, logs can no longer be read, services fail to start.

1. **Do not start another backup** and change as little as possible on the Pi. Every access can damage the card further.
2. **Shut down the Pi**, put the card into a card reader on your PC and take an image with `ddrescue`. Unlike `dd`, it skips damaged areas and retries them later. Check the device name with `lsblk` first:
   ```bash
   sudo ddrescue -d -r3 /dev/sdX klipper-sd.img klipper-sd.map
   ```
3. Write the image to a **new, good-quality card** and check the file systems with `fsck`. Alternatively, set up Klipper from scratch and copy only `printer_data/config` from the image.

If a backup instead reports “Input/output error while accessing the backup destination”, the problem is the NAS or the USB drive, not the card.

### Support and logs

When reporting a problem, include the KIBM version from the dashboard, the affected workflow, and relevant sanitized output from `sudo kibm-admin status` or `sudo kibm-admin logs`. Do not share passwords, session tokens, or private keys.

## Development setup

Python 3.9 or newer is required for local development:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
uvicorn kibm.main:app --app-dir backend --host 127.0.0.1 --port 8765
```

The local development server does not need root privileges, but it cannot perform USB operations or Pi PAM sign-in without the corresponding system services.

## Project status and planned features

- The repository implements the dashboard, diagnostics, local HTTPS, Pi account sign-in, PC image streaming, USB backups, and PiShrink workflows.
- Backup and USB workflows still need validation on each Pi, operating system, and USB configuration.
- A direct **KIBM button in Fluidd and Mainsail** is planned for a future change; the installer does not currently add one.
- Recovery boot and image restoration are unavailable. The recovery directory currently contains planning and validation tools only; KIBM does not install a recovery bootloader or change the boot partition.

## License

MIT. See [LICENSE](LICENSE).

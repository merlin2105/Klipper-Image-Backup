# Recovery status and tryboot plan

## Current status

A plan-only tool is available as `kibm-recovery plan` and `kibm-recovery plan-image`. It validates a supplied gzip-compressed CPIO `newc` initramfs, requires one executable regular `/init`, and creates a separate candidate bundle with:

- `candidate/tryboot.txt`
- `candidate/kibm-recovery.cmdline`
- `candidate/kibm-recovery.cpio.gz`
- `manifest.json` with SHA-256 fingerprints for the normal boot files and generated candidates

The command reads `config.txt` and `cmdline.txt`; it never writes to the supplied boot directory. It rejects an existing `autoboot.txt` or `tryboot.txt`, because those can change boot selection. The output directory must be new and outside the boot tree.

For a raw disk image, `plan-image` reads the partition table and exactly one FAT boot partition using `sfdisk`, `blkid`, and mtools. It does not mount the image, use loop devices, or write to the image. The host needs those tools installed. Only the root `config.txt`, `cmdline.txt`, `autoboot.txt`, and `tryboot.txt` are inspected; boot files over 1 MiB are rejected.

```sh
kibm-recovery plan-image \
  --image ./Klipper-test.img \
  --initramfs ./build/kibm-recovery.cpio.gz \
  --output-dir ./build/tryboot-plan
```

Use the test image or a copy. This command still only emits a plan; it does not boot or alter the image.

Example, using a **copy** of a Pi boot partition and a separately built initramfs:

```sh
kibm-recovery plan \
  --boot-dir ./lab/boot-copy \
  --initramfs ./build/kibm-recovery.cpio.gz \
  --output-dir ./build/tryboot-plan
```

This creates a review bundle only. It does not install files, change EEPROM state, call `reboot`, mount or scan a block device, start a recovery server, or make a backup. The repository does not yet include a Pi-specific initramfs builder or a working `/init` environment. The tool's checks only establish archive structure, not that the kernel can boot it.

## Boot-flow facts and constraints

Raspberry Pi's documented `tryboot` flag is one-shot: firmware clears it before starting the alternate configuration. It loads `tryboot.txt` in place of `config.txt`; after a subsequent reset, the normal configuration is selected again. Pi 4 Model B revision 1.0 and 1.1 require EEPROM write protection to be disabled for this flag. Secure Boot uses `tryboot.img`, which this candidate does not support. The [official boot documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#fail-safe-os-updates-tryboot) describes these constraints.

The candidate config includes the normal `config.txt`, disables automatic loading of the normal OS initramfs, then selects a separate recovery command line and initramfs. Raspberry Pi documents the `initramfs` config option as an advanced technique that is not officially supported with instructions; the [config.txt reference](https://www.raspberrypi.com/documentation/computers/config_txt.html#initramfs) explains its syntax. The plan is therefore not a validated deployment recipe.

`panic=10` asks the kernel to reboot after a kernel panic. It does not recover from a hung kernel or a recovery process that stops responding. No watchdog is enabled or claimed. A hardware test must prove boot, Ethernet DHCP, status UI, read-only SD detection, and return to normal Klipper before any installer or service can activate this candidate.

## Hardware test gate

Before a deployer is added, test at least:

1. Raspberry Pi 4 board revision and EEPROM write-protect state.
2. Raspberry Pi OS release, 32/64-bit kernel, actual boot mount, and current boot config.
3. Recovery kernel and initramfs boot, RAM use, Ethernet DHCP, and status page.
4. Recovery crash, forced reset, and power removal; verify the next normal boot returns to Klipper.
5. Source SD remains unmounted during recovery and is unchanged by the prototype.

No Raspberry Pi is available in this development environment; these hardware checks have not run. No boot files have been changed on any device.

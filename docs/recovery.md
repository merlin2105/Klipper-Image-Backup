# Recovery status

Recovery boot is not implemented. No boot files have been changed by the project.

The current design candidate uses Raspberry Pi's one-shot `tryboot` path to load a separate recovery kernel/initramfs selection while leaving the normal `config.txt` intact. The Raspberry Pi documentation describes the one-shot flag and its fallback behavior, but KIBM has not validated a concrete initramfs on any Pi 4. The installer must remain disabled until a prototype boots, brings up the network, detects the source card read-only, and returns to Klipper after a normal reboot across the supported hardware matrix.

If a prototype is installed for manual lab work, retain a boot-partition backup and ensure physical access and a tested way to select the normal boot configuration. Do not rely on remote-only access during these experiments.

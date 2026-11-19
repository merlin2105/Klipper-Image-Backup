#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Bitte mit sudo ausführen: sudo ./uninstall.sh" >&2
  exit 1
fi
echo "Entfernt KIBM-Dienst, Programmdateien, Zugangsdaten und den Systembenutzer. Klipper, Moonraker und Bootdateien werden nicht verändert."
read -r -p "KIBM wirklich deinstallieren? [j/N] " answer
[[ ${answer,,} == j || ${answer,,} == ja ]] || { echo "Abgebrochen."; exit 0; }
systemctl disable --now kibm.service 2>/dev/null || true
rm -f /etc/systemd/system/kibm.service
systemctl daemon-reload
rm -rf /opt/klipper-image-backup-manager
rm -f /etc/klipper-image-backup-manager.env
if [[ -f /var/lib/kibm/managed-user ]]; then
  userdel kibm 2>/dev/null || true
  rm -f /var/lib/kibm/managed-user
  rmdir /var/lib/kibm 2>/dev/null || true
fi
echo "KIBM entfernt."

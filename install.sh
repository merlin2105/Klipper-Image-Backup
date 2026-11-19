#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Bitte mit sudo ausführen: sudo ./install.sh" >&2
  exit 1
fi
umask 022
if [[ ! -r /etc/os-release ]] || ! . /etc/os-release; then
  echo "Betriebssystem konnte nicht über /etc/os-release erkannt werden." >&2
  exit 1
fi
os_id=${ID:-}
os_id=${os_id,,}
os_like_value=${ID_LIKE:-}
os_like=" ${os_like_value,,} "
debian_compatible=false
if [[ ${os_id} == debian || ${os_like} == *" debian "* ]]; then
  debian_compatible=true
elif [[ ${os_id} == mainsailos && -r /etc/debian_version ]] && command -v apt-get >/dev/null && command -v dpkg-query >/dev/null; then
  debian_compatible=true
fi
if [[ ${debian_compatible} != true ]]; then
  echo "Nicht unterstütztes Betriebssystem: ${PRETTY_NAME:-unbekannt} (ID=${ID:-nicht gesetzt}, ID_LIKE=${ID_LIKE:-nicht gesetzt})." >&2
  echo "KIBM unterstützt derzeit Debian und Debian-basierte Systeme; MainsailOS wird auf Raspberry Pi 4 unterstützt." >&2
  exit 1
fi
if [[ ! -f pyproject.toml || ! -d backend/kibm ]]; then
  echo "Bitte install.sh aus dem Repository-Verzeichnis starten." >&2
  exit 1
fi
model=$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)
if [[ ${model} != *"Raspberry Pi 4"* ]]; then
  echo "Nicht unterstützte Hardware: ${model:-Raspberry-Pi-Modell nicht erkannt}. Dieser frühe Installer ist auf Raspberry Pi 4 begrenzt." >&2
  exit 1
fi
printf 'Hardware: %s\nBetriebssystem: %s\nKernel/Architektur: %s / %s\n' "$model" "${PRETTY_NAME:-Debian-basiert}" "$(uname -r)" "$(uname -m)"
printf 'Boot-Mounts (nur gelesen):\n'
findmnt -rn -o SOURCE,TARGET,FSTYPE /boot /boot/firmware 2>/dev/null || true
printf 'Partitionen (nur gelesen):\n'
lsblk -f 2>/dev/null || true
if systemctl is-active --quiet moonraker.service; then echo "Moonraker: aktiv"; else echo "Moonraker: nicht als moonraker.service aktiv"; fi
if systemctl list-units --type=service --all --no-legend 'klipper*.service' 2>/dev/null | grep -q .; then echo "Klipper: systemd-Dienst erkannt"; else echo "Klipper: systemd-Dienst nicht erkannt"; fi
if [[ -d /var/www/mainsail || -d /usr/share/nginx/html/mainsail ]]; then echo "Mainsail: erkannt"; else echo "Mainsail: nicht erkannt"; fi
if [[ -d /var/www/fluidd || -d /usr/share/nginx/html/fluidd ]]; then echo "Fluidd: erkannt"; else echo "Fluidd: nicht erkannt"; fi

echo "KIBM installiert nur die lesende Diagnoseversion. Bootdateien, Partitionen und EEPROM bleiben unangetastet."
echo "Der Dienst ist im lokalen Netzwerk unter http://<drucker-ip>:8765 erreichbar. Für dieses HTTP-Alpha keine Portweiterleitung ins Internet einrichten."
read -r -p "Installation fortsetzen? [j/N] " answer
[[ ${answer,,} == j || ${answer,,} == ja ]] || { echo "Abgebrochen."; exit 0; }

if ! command -v python3 >/dev/null || ! python3 -c 'import venv' >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip
fi
python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  echo "Python ${python_version} ist zu alt. KIBM benötigt Python 3.9 oder neuer." >&2
  exit 1
fi

install -d -m 0755 /opt/klipper-image-backup-manager
if ! id kibm >/dev/null 2>&1; then
  useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin kibm
  install -d -m 0755 /var/lib/kibm
  touch /var/lib/kibm/managed-user
elif [[ ! -f /var/lib/kibm/managed-user ]]; then
  echo "Systembenutzer kibm existiert bereits; aus Sicherheitsgründen wird nicht fortgefahren." >&2
  exit 1
fi
rm -f /etc/klipper-image-backup-manager.env

install -d -m 0755 /opt/klipper-image-backup-manager/backend
cp -a backend/kibm /opt/klipper-image-backup-manager/backend/
install -m 0644 pyproject.toml /opt/klipper-image-backup-manager/pyproject.toml
python3 -m venv /opt/klipper-image-backup-manager/venv
/opt/klipper-image-backup-manager/venv/bin/pip install --upgrade pip
/opt/klipper-image-backup-manager/venv/bin/pip install /opt/klipper-image-backup-manager
chmod -R a+rX /opt/klipper-image-backup-manager/venv
install -m 0644 systemd/kibm.service /etc/systemd/system/kibm.service
systemctl daemon-reload
systemctl enable --now kibm.service
ip_address=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "KIBM-Diagnose läuft unter http://${ip_address:-<drucker-ip>}:8765"
echo "Backups und Recovery-Boot sind in dieser Version nicht verfügbar."

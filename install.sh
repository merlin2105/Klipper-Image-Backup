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

echo "PC-Ziel: vollständiges, unkomprimiertes Image direkt im Browser speichern. USB-Ziel: Stick wird nach ausdrücklicher Auswahl als exFAT formatiert; Shrinking ist optional."
echo "Die laufende System-microSD wird nicht verändert. USB-Formatierung löscht alle Daten auf dem ausgewählten Stick."
echo "Der Dienst nutzt HTTPS im lokalen Netzwerk. Beim ersten Browseraufruf muss das lokale Zertifikat bestätigt werden. Keine Portweiterleitung ins Internet einrichten."
read -r -p "Installation fortsetzen? [j/N] " answer
[[ ${answer,,} == j || ${answer,,} == ja ]] || { echo "Abgebrochen."; exit 0; }
if mountpoint -q /run/kibm-media/volume 2>/dev/null; then
  echo "Ein USB-Image ist noch eingebunden. Bitte den Stick zuerst in KIBM sicher auswerfen; die Installation wurde nicht verändert." >&2
  exit 1
fi

missing_packages=()
for package in python3 python3-venv python3-pip python3-pam openssl exfatprogs parted e2fsprogs util-linux; do
  if ! dpkg-query -W -f='${Status}' "${package}" 2>/dev/null | grep -q 'install ok installed'; then
    missing_packages+=("${package}")
  fi
done
if (( ${#missing_packages[@]} )); then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing_packages[@]}"
fi
if ! modprobe loop; then
  echo "Das Kernel-Loop-Gerät für PiShrink konnte nicht geladen werden." >&2
  exit 1
fi
python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  echo "Python ${python_version} ist zu alt. KIBM benötigt Python 3.9 oder neuer." >&2
  exit 1
fi
admin_user=${SUDO_USER:-}
if [[ -z ${admin_user} || ${admin_user} == root ]] || ! id "${admin_user}" >/dev/null 2>&1 || (( $(id -u "${admin_user}") < 1000 )); then
  echo "Der Pi-Benutzer konnte nicht erkannt werden. Bitte direkt als Benutzer anmelden und mit 'sudo ./install.sh' installieren." >&2
  exit 1
fi

install -d -m 0755 /opt/klipper-image-backup-manager
if ! id kibm >/dev/null 2>&1; then
  useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin kibm
  install -d -o root -g root -m 0755 /var/lib/kibm
  touch /var/lib/kibm/managed-user
elif [[ ! -f /var/lib/kibm/managed-user ]]; then
  IFS=: read -r _ _ kibm_uid _ _ kibm_home kibm_shell <<< "$(getent passwd kibm)"
  if [[ -z ${kibm_uid} || ${kibm_home} != /nonexistent || ${kibm_shell} != /usr/sbin/nologin ]] || (( kibm_uid >= 1000 )); then
    echo "Systembenutzer kibm existiert bereits und entspricht nicht dem KIBM-Dienstkonto; aus Sicherheitsgründen wird nicht fortgefahren." >&2
    exit 1
  fi
  install -d -o root -g root -m 0755 /var/lib/kibm
  touch /var/lib/kibm/managed-user
fi
rm -f /etc/klipper-image-backup-manager.env
install -d -o root -g kibm -m 0750 /etc/kibm
printf '%s\n' "$admin_user" > /etc/kibm/admin-user
chown root:root /etc/kibm/admin-user
chmod 0644 /etc/kibm/admin-user
printf 'auth    include common-auth\naccount include common-account\n' > /etc/pam.d/kibm
chmod 0644 /etc/pam.d/kibm
printer_ip=$(hostname -I 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) {print $i; exit}}')
printer_name=$(hostname -s)
if [[ -z ${printer_ip} ]]; then
  echo "Keine IPv4-Adresse für das lokale HTTPS-Zertifikat erkannt." >&2
  exit 1
fi
cert_valid=false
if [[ -s /etc/kibm/tls.crt && -s /etc/kibm/tls.key ]] \
  && openssl x509 -in /etc/kibm/tls.crt -noout -checkend 2592000 >/dev/null 2>&1 \
  && openssl x509 -in /etc/kibm/tls.crt -noout -ext subjectAltName 2>/dev/null | grep -Fq "IP Address:${printer_ip}" \
  && [[ $(openssl x509 -in /etc/kibm/tls.crt -pubkey -noout 2>/dev/null | openssl sha256) == $(openssl pkey -in /etc/kibm/tls.key -pubout 2>/dev/null | openssl sha256) ]]; then
  cert_valid=true
fi
if [[ ${cert_valid} != true ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 825 \
    -keyout /etc/kibm/tls.key.new -out /etc/kibm/tls.crt.new \
    -subj "/CN=${printer_name}" \
    -addext "subjectAltName=DNS:${printer_name},DNS:${printer_name}.local,IP:${printer_ip},IP:127.0.0.1"
  mv /etc/kibm/tls.key.new /etc/kibm/tls.key
  mv /etc/kibm/tls.crt.new /etc/kibm/tls.crt
  chown root:kibm /etc/kibm/tls.key /etc/kibm/tls.crt
  chmod 0640 /etc/kibm/tls.key
  chmod 0644 /etc/kibm/tls.crt
fi
chown root:kibm /etc/kibm/tls.key /etc/kibm/tls.crt
chmod 0640 /etc/kibm/tls.key
chmod 0644 /etc/kibm/tls.crt

systemctl stop kibm.service 2>/dev/null || true
systemctl stop kibm-auth.service 2>/dev/null || true
systemctl stop kibm-media.service 2>/dev/null || true
install -d -m 0755 /etc/modules-load.d
printf 'loop\n' > /etc/modules-load.d/kibm-loop.conf
install -d -m 0755 /opt/klipper-image-backup-manager/backend
cp -a backend/kibm /opt/klipper-image-backup-manager/backend/
install -d -m 0755 /opt/klipper-image-backup-manager/tools
install -m 0755 tools/pishrink.sh /opt/klipper-image-backup-manager/tools/pishrink.sh
install -m 0644 tools/PISHRINK-LICENSE /opt/klipper-image-backup-manager/tools/PISHRINK-LICENSE
install -m 0644 pyproject.toml /opt/klipper-image-backup-manager/pyproject.toml
install -m 0644 systemd/kibm-auth.service /etc/systemd/system/kibm-auth.service
install -m 0644 systemd/kibm-media.service /etc/systemd/system/kibm-media.service
python3 -m venv /opt/klipper-image-backup-manager/venv
/opt/klipper-image-backup-manager/venv/bin/pip install --upgrade pip
/opt/klipper-image-backup-manager/venv/bin/pip install /opt/klipper-image-backup-manager
chmod -R a+rX /opt/klipper-image-backup-manager/venv
install -m 0644 systemd/kibm.service /etc/systemd/system/kibm.service
systemctl daemon-reload
systemctl enable --now kibm-auth.service
if ! systemctl is-active --quiet kibm-auth.service || [[ ! -S /run/kibm-auth/auth.sock ]]; then
  echo "Der Pi-Passwortdienst konnte seinen lokalen Socket nicht starten." >&2
  systemctl status kibm-auth.service --no-pager -l >&2 || true
  journalctl -u kibm-auth.service -n 30 --no-pager >&2 || true
  exit 1
fi
systemctl enable --now kibm-media.service
if ! systemctl is-active --quiet kibm-media.service || [[ ! -S /run/kibm-media/worker.sock ]]; then
  echo "Der USB-Image-Dienst konnte seinen lokalen Socket nicht starten." >&2
  systemctl status kibm-media.service --no-pager -l >&2 || true
  journalctl -u kibm-media.service -n 30 --no-pager >&2 || true
  exit 1
fi
systemctl enable --now kibm.service
if ! python3 - <<'PY'
import time
import urllib.error
import urllib.request

deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    try:
        context = __import__("ssl")._create_unverified_context()
        with urllib.request.urlopen("https://127.0.0.1:8765/api/system", timeout=2, context=context) as response:
            if response.status == 200:
                raise SystemExit(0)
    except (OSError, urllib.error.URLError):
        time.sleep(1)
raise SystemExit(1)
PY
then
  echo "KIBM wurde installiert, aber der Webdienst antwortet nicht." >&2
  systemctl status kibm.service --no-pager -l >&2 || true
  journalctl -u kibm.service -n 30 --no-pager >&2 || true
  exit 1
fi
ip_address=$(hostname -I 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) {print $i; exit}}')
echo "Installation erfolgreich. KIBM-Webportal im Browser öffnen:"
echo "https://${ip_address:-<drucker-ip>}:8765"
echo "Port 90 bleibt für die Klipper-Oberfläche (Mainsail/Fluidd)."
echo "Beim ersten Browseraufruf das lokale Zertifikat einmalig bestätigen."

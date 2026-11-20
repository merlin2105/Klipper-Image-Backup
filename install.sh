#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Bitte mit sudo ausführen: sudo ./install.sh" >&2
  exit 1
fi
umask 022
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
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
if [[ ${KIBM_INSTALL_CONFIRM:-} == 1 ]]; then
  answer=j
else
  read -r -p "Installation fortsetzen? [j/N] " answer
fi
[[ ${answer,,} == j || ${answer,,} == ja ]] || { echo "Abgebrochen."; exit 0; }
install_step="Voraussetzungen prüfen"
units=(kibm-auth.service kibm-media.service kibm.service)
report_failure() {
  echo "Installation fehlgeschlagen: ${install_step}." >&2
  if [[ $install_step == "KIBM starten und prüfen" ]]; then
    systemctl status "${units[@]}" --no-pager -l >&2 || true
    journalctl -b -u kibm.service -u kibm-auth.service -u kibm-media.service -n 35 --no-pager >&2 || true
  fi
}
trap report_failure ERR

media_pid=$(systemctl show kibm-media.service -p MainPID --value 2>/dev/null || true)
if mountpoint -q /run/kibm-media/volume 2>/dev/null ||
  { [[ $media_pid =~ ^[1-9][0-9]*$ ]] && awk '$5 == "/run/kibm-media/volume" {found=1} END {exit !found}' "/proc/$media_pid/mountinfo"; }; then
  echo "Bitte zuerst den USB-Stick in KIBM sicher auswerfen und dann das Update erneut starten." >&2
  exit 1
fi

install_step="Systempakete installieren"
missing_packages=()
for package in python3 python3-venv python3-pip python3-pam openssl exfatprogs parted e2fsprogs util-linux mount udev kmod curl; do
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
if [[ ! ${admin_user} =~ ^[a-z_][a-z0-9_-]*[$]?$ ]]; then
  echo "Der Pi-Benutzername enthält Zeichen, die nicht sicher in sudoers verwendet werden können." >&2
  exit 1
fi

install_step="Dienstkonto und Zugriffsrechte einrichten"
install_dir=/opt/klipper-image-backup-manager
install -d -o root -g root -m 0755 "$install_dir"
install -o root -g root -m 0755 scripts/kibm-admin /usr/local/sbin/kibm-admin
sudoers_tmp=$(mktemp /etc/sudoers.d/kibm-admin.XXXXXX)
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/kibm-admin\n' "$admin_user" > "$sudoers_tmp"
chmod 0440 "$sudoers_tmp"
if ! visudo -cf "$sudoers_tmp"; then
  rm -f "$sudoers_tmp"
  echo "KIBM-Verwaltungsfreigabe konnte nicht sicher geprüft werden." >&2
  exit 1
fi
mv "$sudoers_tmp" /etc/sudoers.d/kibm-admin
if ! id kibm >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin kibm
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
install_step="HTTPS-Adresse und Zertifikat einrichten"
printer_ip=$(hostname -I 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) {print $i; exit}}')
printer_name=$(hostname -s)
if [[ -z ${printer_ip} ]]; then
  echo "Keine IPv4-Adresse für das lokale HTTPS-Zertifikat erkannt." >&2
  exit 1
fi
cert_valid=false
if [[ -s /etc/kibm/tls.crt && -s /etc/kibm/tls.key ]] \
  && openssl x509 -in /etc/kibm/tls.crt -noout -checkend 2592000 >/dev/null 2>&1 \
  && openssl x509 -in /etc/kibm/tls.crt -noout -ext subjectAltName 2>/dev/null | grep -oE "IP Address:[0-9.]+" | grep -Fx "IP Address:${printer_ip}" >/dev/null \
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

install_step="Frische Python-Umgebung aufbauen"
venv_path=$(mktemp -d "$install_dir/venv.XXXXXX")
staged_venv=$venv_path
trap 'if [[ -n $staged_venv ]]; then rm -rf -- "$staged_venv"; fi' EXIT
python3 -m venv "$venv_path"
"$venv_path/bin/python" -m pip install --disable-pip-version-check .
chmod -R a+rX "$venv_path"
runuser -u kibm -g kibm -- "$venv_path/bin/python" -c "from kibm.main import app"

install_step="KIBM-Dienste installieren"
old_venv=
if [[ -L $install_dir/venv ]]; then
  old_venv=$(readlink -f "$install_dir/venv")
fi
for service in kibm.service kibm-auth.service kibm-media.service; do
  if systemctl cat "$service" >/dev/null 2>&1; then
    systemctl stop "$service"
  fi
done
if [[ -d $install_dir/venv && ! -L $install_dir/venv ]]; then
  old_venv=$(mktemp -d "$install_dir/venv.XXXXXX")
  mv -T "$install_dir/venv" "$old_venv"
fi
ln -sfn "${venv_path##*/}" "$install_dir/venv.current"
mv -Tf "$install_dir/venv.current" "$install_dir/venv"
staged_venv=
install -d -m 0755 /etc/modules-load.d
printf 'loop\n' > /etc/modules-load.d/kibm-loop.conf
install -d -m 0755 /opt/klipper-image-backup-manager/backend
cp -R --no-preserve=ownership backend/kibm "$install_dir/backend/"
chown -R root:root "$install_dir/backend"
chmod -R a+rX "$install_dir/backend"
install -d -m 0755 /opt/klipper-image-backup-manager/tools
install -m 0755 tools/pishrink.sh /opt/klipper-image-backup-manager/tools/pishrink.sh
install -m 0644 tools/PISHRINK-LICENSE /opt/klipper-image-backup-manager/tools/PISHRINK-LICENSE
install -m 0644 pyproject.toml /opt/klipper-image-backup-manager/pyproject.toml
install -m 0644 systemd/kibm-auth.service /etc/systemd/system/kibm-auth.service
install -m 0644 systemd/kibm-media.service /etc/systemd/system/kibm-media.service
install -m 0644 systemd/kibm.service /etc/systemd/system/kibm.service
install -m 0644 systemd/kibm-update.service /etc/systemd/system/kibm-update.service
build_commit=${KIBM_SOURCE_COMMIT:-}
if [[ ! $build_commit =~ ^[0-9a-f]{40}$ && -r BUILD_COMMIT ]]; then
  build_commit=$(<BUILD_COMMIT)
fi
if [[ ! $build_commit =~ ^[0-9a-f]{40} ]] && command -v git >/dev/null 2>&1; then
  build_commit=$(git rev-parse --verify HEAD 2>/dev/null || true)
fi
if [[ ! $build_commit =~ ^[0-9a-f]{40} ]]; then build_commit=unknown; fi
source_dirty=false
if [[ -z ${KIBM_SOURCE_COMMIT:-} && -d .git ]] && [[ -n $(git status --porcelain --untracked-files=normal 2>/dev/null || true) ]]; then
  source_dirty=true
fi
printf '{"commit":"%s","dirty":%s}\n' "$build_commit" "$source_dirty" > "$install_dir/build-info.json"
chmod 0644 "$install_dir/build-info.json"
systemctl daemon-reload

install_step="KIBM starten und prüfen"
systemctl reset-failed "${units[@]}" 2>/dev/null || true
systemctl enable --now "${units[@]}"
if ! python3 - "$printer_ip" <<'PY'
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

printer_ip = sys.argv[1]
context = ssl._create_unverified_context()
deadline = time.monotonic() + 30
last_error = "keine Antwort"
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(
            f"https://{printer_ip}:8765/api/health", timeout=3, context=context
        ) as response:
            result = json.loads(response.read(65536))
            if response.status == 200 and result.get("ready") is True:
                raise SystemExit(0)
            last_error = f"Readiness-Prüfung meldet {result!r}"
    except (OSError, ValueError, urllib.error.URLError) as error:
        last_error = str(error)
    time.sleep(1)
print(f"KIBM wurde installiert, aber die Bereitschaftsprüfung schlug fehl: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
then
  systemctl status "${units[@]}" --no-pager -l >&2 || true
  journalctl -b -u kibm.service -u kibm-auth.service -u kibm-media.service -n 35 --no-pager >&2 || true
  exit 1
fi
if [[ $old_venv == "$install_dir"/venv.* && $old_venv != "$venv_path" ]]; then
  rm -rf -- "$old_venv"
fi
echo "Installation erfolgreich. KIBM-Webportal im Browser öffnen:"
echo "https://${printer_ip}:8765/"
echo "Beim ersten Browseraufruf das lokale Zertifikat einmalig bestätigen."
echo "KIBM-Administration über SSH: sudo kibm-admin {status|logs|usb|restart|update}"

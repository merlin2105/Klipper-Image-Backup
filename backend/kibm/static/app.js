const $ = (id) => document.getElementById(id);
const USB_FORMAT_HEADROOM_BYTES = 64 * 1024 * 1024;
let csrfToken = "";
let readiness = null;
let destination = null;
let activeJobId = null;
let activeJobType = "pc";
let activeDownloadToken = "";
let usbJobId = null;
let transferPoll = null;
let usbDevices = [];
let usbDeviceSignature = null;
let usbScanInProgress = false;

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method === "POST" && csrfToken) headers.set("X-KIBM-CSRF", csrfToken);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toLocaleString("de-DE", { maximumFractionDigits: 1 })} ${units[unit]}`;
}

function renderMetricMeter(id, percent) {
  const available = Number.isFinite(percent);
  $(`${id}-meter`).hidden = !available;
  if (available) $(`${id}-progress`).value = percent;
}

async function refreshSystemMetrics() {
  let metrics = {};
  try {
    metrics = await api("/api/system/metrics", { signal: AbortSignal.timeout(4000) });
  } catch {
    // Keep diagnostics and backup controls usable when live metrics are unavailable.
  }

  const unavailable = "Nicht verfügbar";
  const number = value => value.toLocaleString("de-DE", { maximumFractionDigits: 1 });
  const cpu = metrics.cpu_percent;
  $("cpu-usage").textContent = Number.isFinite(cpu) ? `${number(cpu)} %` : unavailable;
  renderMetricMeter("cpu", cpu);

  const memory = metrics.memory;
  const ramPercent = memory?.total_bytes > 0 ? 100 * memory.used_bytes / memory.total_bytes : null;
  $("ram-usage").textContent = Number.isFinite(ramPercent)
    ? `${number(ramPercent)} % · ${formatBytes(memory.used_bytes)} von ${formatBytes(memory.total_bytes)}`
    : unavailable;
  renderMetricMeter("ram", ramPercent);

  const temperature = metrics.cpu_temperature_celsius;
  $("cpu-temperature").textContent = Number.isFinite(temperature) ? `${number(temperature)} °C` : unavailable;
  const uptime = metrics.uptime_seconds;
  if (Number.isFinite(uptime)) {
    const days = Math.floor(uptime / 86400);
    const hours = Math.floor(uptime / 3600) % 24;
    const minutes = Math.floor(uptime / 60) % 60;
    $("system-uptime").textContent = days ? `${days} T ${hours} Std` : `${hours} Std ${minutes} Min`;
  } else {
    $("system-uptime").textContent = unavailable;
  }
}

async function pollSystemMetrics() {
  if (document.visibilityState === "visible") await refreshSystemMetrics();
  window.setTimeout(pollSystemMetrics, 5000);
}

function showError(message) {
  $("overall-title").textContent = "Systemprüfung nicht vollständig";
  $("overall-description").textContent = message;
  $("overall-led").className = "status-led warning";
}

async function loadDashboard() {
  try {
    const [system, storage, printer, currentReadiness, session] = await Promise.all([
      api("/api/system"), api("/api/storage"), api("/api/klipper/status"), api("/api/backup/preflight"), api("/api/session"),
    ]);
    $("hostname").textContent = system.hostname;
    $("host-name").textContent = system.hostname.toUpperCase();
    $("model").textContent = system.model;
    $("os-name").textContent = system.os;
    $("kernel").textContent = system.kernel;
    $("architecture").textContent = system.architecture;
    csrfToken = session.authenticated ? session.csrf_token : "";
    readiness = currentReadiness;
    $("overall-title").textContent = "Diagnose aktiv · Systemkarte geschützt";
    $("overall-description").textContent = currentReadiness.backup_available
      ? (printer.classification === "unknown"
        ? "Kein Druckerstatus verfügbar · Backup nach Bestätigung möglich · vollständige System-microSD erkannt."
        : "Drucker im Standby · vollständige System-microSD erkannt.")
      : currentReadiness.blockers.join(" ");
    $("overall-led").className = currentReadiness.backup_available ? "status-led" : "status-led warning";
    renderPrinter(printer);
    renderStorage(storage);
    renderReadiness(currentReadiness);
    await loadUsbDevices();
    updateDestination();
    $("footer-time").textContent = `AKTUALISIERT ${new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`;
  } catch (error) {
    showError(error.message);
  }
}

function renderReadiness(result) {
  const checks = result.checks || {};
  const printer = result.printer || {};
  const printerActive = printer.classification === "active";
  const printerUnknown = printer.classification === "unknown";
  setCapability("printer-capability", "Druckerstatus", !printerActive, printerActive ? "Druck aktiv · gesperrt" : printerUnknown ? "nicht verfügbar · Bestätigung nötig" : "Standby", printerUnknown);
  setCapability("storage-capability", "System-microSD", checks.system_card_supported, checks.system_card_supported ? "unterstützt" : "nicht unterstützt");
  setCapability("read-capability", "Leserechte", checks.read_access, checks.read_access ? "bereit" : "fehlen");
  const size = result.image_size_bytes;
  $("image-estimate").textContent = size ? `${formatBytes(size)} · vollständige Karte` : "Größe nicht verfügbar";
  $("image-estimate-badge").textContent = result.backup_available ? "BEREIT" : "GESPERRT";
  $("image-estimate-badge").classList.toggle("estimate-ready", result.backup_available);
  $("preflight-message").textContent = result.backup_available
    ? (printerUnknown
      ? "Kein Druckerstatus verfügbar. Starte nur, wenn sicher kein Druck läuft; die Bestätigung ist erforderlich."
      : "")
    : result.blockers.join(" ");
  updateDestination();
}

function isUsbDestination() {
  return destination === "usb";
}

async function loadUsbDevices() {
  if (usbScanInProgress) return;
  usbScanInProgress = true;
  let signature;
  try {
    const result = await api("/api/backup/usb/devices");
    usbDevices = result.devices || [];
    signature = JSON.stringify(usbDevices.map(({ path, size_bytes, model }) => [path, size_bytes, model]).sort());
    if (signature !== usbDeviceSignature) {
      const selectedPath = $("usb-device").value;
      $("usb-device").replaceChildren(new Option("USB-Stick auswählen", ""));
      for (const device of usbDevices) {
        const detail = device.model?.trim() || device.path;
        $("usb-device").append(new Option(`${detail} · ${formatBytes(device.size_bytes)}`, device.path));
      }
      if (usbDevices.some((device) => device.path === selectedPath)) $("usb-device").value = selectedPath;
    }
  } catch (error) {
    usbDevices = [];
    signature = "unavailable";
    if (signature !== usbDeviceSignature) $("usb-device").replaceChildren(new Option("USB-Erkennung nicht verfügbar", ""));
  }
  const changed = signature !== usbDeviceSignature;
  usbDeviceSignature = signature;
  renderUsbDetection();
  if (changed) {
    $("format-confirm").checked = false;
    updateDestination();
  }
  usbScanInProgress = false;
}

function renderUsbDetection() {
  const status = $("usb-detection-status");
  const device = selectedUsbDevice();
  let title = "Kein USB-Stick erkannt";
  let detail = "Sobald du einen Stick am Pi einsteckst, erscheint er hier.";
  let state = "";
  if (usbDeviceSignature === "unavailable") {
    title = "USB-Erkennung nicht erreichbar";
    detail = "Die Erkennung wird automatisch erneut versucht.";
    state = "unavailable";
  } else if (device) {
    title = `${device.model?.trim() || "USB-Stick"} ausgewählt`;
    detail = `${device.path} · Vor der Sicherung ist eine Formatierung als exFAT nötig. Sie löscht die vorhandenen Daten.`;
    state = "detected";
  } else if (usbDevices.length) {
    title = usbDevices.length === 1 ? "USB-Stick erkannt" : `${usbDevices.length} USB-Sticks erkannt`;
    detail = isUsbDestination()
      ? "Wähle den Stick in der Liste aus."
      : "Wähle „USB-Stick · Sicherung“, um ein Image darauf zu speichern.";
    state = "detected";
  }
  status.dataset.state = state;
  const heading = $("usb-detection-title");
  const description = $("usb-detection-detail");
  if (heading.textContent !== title) heading.textContent = title;
  if (description.textContent !== detail) description.textContent = detail;
}

function selectedUsbDevice() {
  return usbDevices.find((device) => device.path === $("usb-device").value) || null;
}

function requiredUsbCapacity(shrink) {
  const imageSize = Number(readiness?.image_size_bytes);
  if (!Number.isFinite(imageSize) || imageSize <= 0) return 0;
  return imageSize * (shrink ? 2 : 1) + USB_FORMAT_HEADROOM_BYTES;
}

function updateDestination() {
  const usbMode = isUsbDestination();
  const device = selectedUsbDevice();
  const shrink = $("shrink-image").checked;
  const running = Boolean(activeJobId);
  $("destination-pc").disabled = running;
  $("destination-usb").disabled = running || usbDevices.length === 0;
  $("destination-pc").setAttribute("aria-pressed", String(destination === "pc"));
  $("destination-usb").setAttribute("aria-pressed", String(usbMode));
  $("pc-options").hidden = destination !== "pc";
  $("usb-options").hidden = !usbMode;
  $("keep-full-row").hidden = !shrink;
  $("backup-confirmations").hidden = !destination;
  $("format-confirm-row").hidden = !usbMode || !device;
  $("start-backup").hidden = !destination;
  const ready = Boolean(readiness?.backup_available);
  const minimumUsbBytes = requiredUsbCapacity(shrink);
  const targetTooSmall = Boolean(usbMode && device && minimumUsbBytes && device.size_bytes < minimumUsbBytes);
  $("start-backup").disabled = !destination || !ready || running || !$("backup-confirm").checked
    || (usbMode && (!device || targetTooSmall || !$("format-confirm").checked));
  let capacityMessage = "";
  if (targetTooSmall) {
    const gib = bytes => `${(bytes / 1024 ** 3).toLocaleString("de-DE", { maximumFractionDigits: 2 })} GiB`;
    capacityMessage = `Zu wenig Platz: Der Stick hat ${gib(device.size_bytes)}. Diese Auswahl benötigt rund ${gib(minimumUsbBytes)}.`;
    if (shrink) capacityMessage += " PiShrink braucht während der Verarbeitung Platz für zwei vollständige Kopien plus Reserve.";
    capacityMessage += shrink && device.size_bytes >= requiredUsbCapacity(false)
      ? " Entferne den Haken bei „Image … verkleinern“, um das vollständige Image auf diesem Stick zu sichern."
      : " Wähle einen größeren Stick oder sichere das Image auf dem PC.";
  }
  $("usb-capacity-message").textContent = capacityMessage;
}

function setCapability(id, label, enabled, detail, warning = false) {
  const row = $(id);
  const icon = row.querySelector("i");
  icon.className = warning ? "cap-warn" : enabled ? "cap-yes" : "cap-no";
  icon.replaceChildren();
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", warning ? "M8 3v6m0 3h.01" : enabled ? "m3 8 3 3 7-7" : "m4 4 8 8m0-8-8 8");
  svg.append(path);
  icon.append(svg);
  row.querySelector(".capability-label").textContent = label;
  row.querySelector("b").textContent = detail;
}

function renderPrinter(printer) {
  const isReady = printer.classification === "ready";
  const isActive = printer.classification === "active";
  $("printer-icon").className = `printer-icon ${isReady ? "ready" : isActive ? "active" : "unknown"}`;
  $("printer-icon-path").setAttribute("d", isReady ? "m5 12 4 4L19 6" : isActive ? "M8 5v14m8-14v14" : "M12 7v6m0 4h.01");
  $("printer-title").textContent = isReady ? "Bereit" : isActive ? "Druckauftrag aktiv" : "Status unbekannt";
  $("printer-description").textContent = printer.message;
  $("printer-state").textContent = (printer.state || "UNBEKANNT").toUpperCase();
  $("restart-safety").textContent = `BACKUP: ${isActive ? "GESPERRT" : printer.classification === "unknown" ? "BESTÄTIGUNG NÖTIG" : "BEREIT"}`;
}

function renderStorage(storage) {
  const disk = storage.system_device;
  $("storage-status").textContent = storage.available ? "ERKANNT" : "NICHT ERKANNT";
  $("disk-name").textContent = disk ? disk.path : "Systemlaufwerk unbekannt";
  $("disk-model").textContent = disk?.model?.trim() || "Modell nicht gemeldet";
  $("disk-size").textContent = disk ? formatBytes(disk.size_bytes) : "—";
  const usage = storage.filesystem_usage;
  if (usage) {
    const usedPct = usage.total_bytes ? Math.min(100, usage.used_bytes / usage.total_bytes * 100) : 0;
    $("usage-value").textContent = `${Math.round(usedPct)} %`;
    $("usage-bar-fill").value = usedPct;
    $("used-size").textContent = formatBytes(usage.used_bytes);
    $("free-size").textContent = formatBytes(usage.free_bytes);
  }
  if (!storage.system_device) $("image-estimate").textContent = "Systemlaufwerk nicht erkannt";
  const partitions = storage.devices.filter((device) => device.type === "part");
  $("partition-count").textContent = String(partitions.length).padStart(2, "0");
  $("partition-rows").replaceChildren();
  if (!partitions.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "empty-row";
    cell.textContent = "Keine Partitionen erkannt.";
    row.append(cell);
    $("partition-rows").append(row);
  }
  for (const partition of partitions) {
    const row = document.createElement("tr");
    for (const value of [partition.path, partition.filesystem || "—", formatBytes(partition.size_bytes), (partition.mountpoints || []).filter(Boolean).join(", ") || "—"]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    $("partition-rows").append(row);
  }
}

$("preflight").addEventListener("click", async () => {
  $("preflight-message").textContent = "Prüfung läuft …";
  try {
    const result = await api("/api/backup/preflight");
    readiness = result;
    renderReadiness(result);
    renderPrinter(result.printer);
    renderStorage(result.storage);
    await loadUsbDevices();
    updateDestination();
  } catch (error) { $("preflight-message").textContent = error.message; }
});

for (const target of ["pc", "usb"]) {
  $(`destination-${target}`).addEventListener("click", () => {
    if (destination !== target) {
      destination = target;
      $("backup-confirm").checked = false;
      $("format-confirm").checked = false;
    }
    renderUsbDetection();
    updateDestination();
  });
}
$("backup-confirm").addEventListener("change", updateDestination);
$("format-confirm").addEventListener("change", updateDestination);
$("usb-device").addEventListener("change", () => {
  $("format-confirm").checked = false;
  renderUsbDetection();
  updateDestination();
});
$("shrink-image").addEventListener("change", updateDestination);
$("refresh-usb").addEventListener("click", loadUsbDevices);

$("start-backup").addEventListener("click", async () => {
  try {
    const session = await api("/api/session");
    if ($("start-backup").disabled || $("backup-login").open) return;
    csrfToken = session.authenticated ? session.csrf_token : "";
    $("pi-auth-fields").hidden = session.authenticated;
    $("backup-dialog-title").textContent = session.authenticated ? "Image-Erstellung bestätigen" : "Mit deinem Pi-Konto anmelden";
    $("dialog-submit-label").textContent = isUsbDestination() ? "USB-Image erstellen" : "Image herunterladen";
    $("backup-login-error").textContent = "";
    $("pi-password").required = !session.authenticated;
    $("pi-password").value = "";
    if (isUsbDestination()) {
      const target = selectedUsbDevice();
      $("backup-dialog-detail").textContent = target
        ? `${target.path} · ${target.model?.trim() || "Modell unbekannt"} · ${formatBytes(target.size_bytes)}. Der Stick wird formatiert; alle vorhandenen Daten darauf werden gelöscht.`
        : "Bitte zuerst einen USB-Stick auswählen.";
    } else {
      $("backup-dialog-detail").textContent = session.authenticated
        ? "Bestätige, dass kein Druck läuft und während der Sicherung keiner startet. Ohne erreichbaren Moonraker-Status ist keine automatische Druckprüfung möglich. Für die PC-Sicherung wird die vollständige Karte übertragen."
        : "Verwende das vorhandene Passwort deines Linux-Kontos. Es wird nicht geändert oder von KIBM gespeichert.";
    }
    $("backup-login").showModal();
  } catch (error) { $("preflight-message").textContent = error.message; }
});

$("backup-cancel").addEventListener("click", () => {
  $("pi-password").value = "";
  $("backup-login").close();
});
$("backup-login").addEventListener("cancel", () => { $("pi-password").value = ""; });

$("backup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.submitter || $("backup-form").querySelector('button[type="submit"]');
  if (submit.disabled) return;
  submit.disabled = true;
  $("backup-login-error").textContent = "";
  try {
    if (!csrfToken) {
      const login = await api("/api/login", {
        method: "POST",
        body: JSON.stringify({ password: $("pi-password").value }),
      });
      csrfToken = login.csrf_token;
      $("pi-password").value = "";
    }
    if (!$("backup-login").open || $("start-backup").disabled) {
      throw new Error("Prüfe Speicherziel und Bestätigungen erneut.");
    }
    let result;
    if (isUsbDestination()) {
      const target = selectedUsbDevice();
      if (!target) throw new Error("Bitte zuerst einen erkannten USB-Stick auswählen.");
      result = await api("/api/backup/usb/start", {
        method: "POST",
        body: JSON.stringify({
          device_path: target.path,
          shrink: $("shrink-image").checked,
          keep_full: $("keep-full-image").checked,
          verify_image: $("verify-image").checked,
          confirmed: $("backup-confirm").checked,
          format_confirmed: $("format-confirm").checked,
        }),
      });
      activeJobType = "usb";
      activeDownloadToken = result.download_token;
      usbJobId = result.job_id;
    } else {
      result = await api("/api/backup/start", {
        method: "POST",
        body: JSON.stringify({ confirmed: $("backup-confirm").checked }),
      });
      activeJobType = "pc";
    }
    $("backup-login").close();
    activeJobId = result.job_id;
    $("backup-confirm").checked = false;
    $("format-confirm").checked = false;
    $("backup-progress").hidden = false;
    $("backup-progress-title").textContent = activeJobType === "usb" ? "USB-Auftrag gestartet" : "Download bereit";
    $("backup-progress-value").textContent = "0 %";
    $("backup-progress-bar").value = 0;
    $("backup-progress-bar").setAttribute("value", "0");
    $("backup-progress-detail").textContent = activeJobType === "usb"
      ? `USB-Stick ${result.device.path} · ${result.message}`
      : `${result.filename} · ${formatBytes(result.size_bytes)}. Wähle den Speicherort im Browser.`;
    const download = $("backup-download");
    download.hidden = activeJobType === "usb";
    $("usb-results").replaceChildren();
    $("usb-results").hidden = true;
    $("usb-eject").hidden = true;
    if (activeJobType === "pc") {
      download.href = result.download_url;
      download.download = result.filename;
      download.hidden = false;
    }
    updateDestination();
    pollTransfer();
  } catch (error) {
    $("pi-password").value = "";
    $("backup-login-error").textContent = error.message;
    if (error.message.includes("Pi-Kontos")) csrfToken = "";
  } finally {
    submit.disabled = false;
  }
});

$("backup-download").addEventListener("click", (event) => {
  if (!activeJobId) { event.preventDefault(); return; }
  $("backup-progress-title").textContent = "Image wird übertragen";
  $("backup-download").hidden = true;
});

async function pollTransfer() {
  if (!activeJobId) return;
  if (activeJobType === "usb") {
    await pollUsbJob();
    return;
  }
  try {
    const status = await api(`/api/backup/jobs/${activeJobId}`);
    const percent = status.size_bytes ? Math.min(100, status.sent_bytes / status.size_bytes * 100) : 0;
    $("backup-progress-bar").value = percent;
    $("backup-progress-value").textContent = `${Math.floor(percent)} %`;
    if (status.state === "übertragen") {
      $("backup-progress-title").textContent = "Übertragung vollständig";
      $("backup-progress-detail").textContent = `${formatBytes(status.sent_bytes)} an den Browser übertragen. SHA-256: ${status.sha256}`;
      activeJobId = null;
      renderReadiness(readiness);
      return;
    }
    if (["fehler", "abgebrochen"].includes(status.state)) {
      $("backup-progress-title").textContent = "Image unvollständig";
      $("backup-progress-detail").textContent = status.error || "Die Übertragung wurde nicht vollständig abgeschlossen.";
      activeJobId = null;
      renderReadiness(readiness);
      return;
    }
    if (status.state === "streaming") {
      $("backup-progress-title").textContent = "Image wird übertragen";
      $("backup-progress-detail").textContent = `${formatBytes(status.sent_bytes)} von ${formatBytes(status.size_bytes)} an den Browser übertragen.`;
    }
    transferPoll = window.setTimeout(pollTransfer, 1000);
  } catch (error) {
    $("backup-progress-title").textContent = "Status nicht erreichbar";
    if (error.message.includes("Pi-Konto")) {
      $("backup-progress-detail").textContent = "Die Sitzung ist abgelaufen. Prüfe den Downloadstatus im Browser und lade die Seite bei Bedarf neu.";
      activeJobId = null;
      renderReadiness(readiness);
      return;
    }
    $("backup-progress-detail").textContent = `${error.message} · erneuter Versuch in 3 Sekunden.`;
    transferPoll = window.setTimeout(pollTransfer, 3000);
  }
}

function showMeasuredProgress(bytes, total) {
  const percent = total ? Math.min(100, bytes / total * 100) : 0;
  $("backup-progress-bar").value = percent;
  $("backup-progress-value").textContent = `${Math.floor(percent)} %`;
}

async function pollUsbJob() {
  if (!activeJobId) return;
  try {
    const status = await api(`/api/backup/usb/jobs/${activeJobId}`);
    $("backup-progress-detail").textContent = status.error || status.message;
    if (status.state === "sichern") {
      const percent = status.image_size_bytes ? Math.min(100, status.sent_bytes / status.image_size_bytes * 100) : 0;
      $("backup-progress-title").textContent = "Vollständiges Image wird auf USB gesichert";
      $("backup-progress-value").textContent = `${Math.floor(percent)} %`;
      $("backup-progress-bar").value = percent;
      $("backup-progress-detail").textContent = `${formatBytes(status.sent_bytes)} von ${formatBytes(status.image_size_bytes)} geschrieben.`;
    } else if (status.state === "schrumpfen") {
      $("backup-progress-title").textContent = "Image wird auf dem Pi verkleinert";
      if (["kopie", "pruefsumme"].includes(status.phase)) {
        const step = status.phase === "kopie" ? 1 : 3;
        showMeasuredProgress(status.shrink_bytes, status.shrink_total_bytes);
        $("backup-progress-detail").textContent = `Schritt ${step}/3 · ${status.message} ${formatBytes(status.shrink_bytes)} von ${formatBytes(status.shrink_total_bytes)}.`;
      } else {
        $("backup-progress-value").textContent = "Schritt 2/3";
        $("backup-progress-bar").removeAttribute("value");
        $("backup-progress-detail").textContent = status.message;
      }
    } else if (status.state === "verifizieren") {
      $("backup-progress-title").textContent = "Image auf dem USB-Stick wird geprüft";
      showMeasuredProgress(status.verified_bytes, status.image_size_bytes);
      $("backup-progress-detail").textContent = `${formatBytes(status.verified_bytes)} von ${formatBytes(status.image_size_bytes)} für SHA-256 gelesen.`;
    } else if (status.state === "formatieren") {
      $("backup-progress-title").textContent = "USB-Stick wird vorbereitet";
      $("backup-progress-value").textContent = "exFAT";
      $("backup-progress-bar").removeAttribute("value");
    } else if (status.state === "bereit") {
      $("backup-progress-title").textContent = "Image auf USB bereit";
      $("backup-progress-value").textContent = "Fertig";
      $("backup-progress-bar").setAttribute("value", "100");
      $("backup-progress-bar").value = 100;
      renderUsbFiles(status.files || [], usbJobId, activeDownloadToken);
      $("usb-eject").hidden = false;
      activeJobId = null;
      updateDestination();
      return;
    } else if (status.state === "fehler") {
      $("backup-progress-title").textContent = "USB-Sicherung fehlgeschlagen";
      $("backup-progress-value").textContent = "Fehler";
      $("backup-progress-bar").removeAttribute("value");
      if ((status.files || []).length) {
        $("backup-progress-detail").textContent = `Der Shrink ist fehlgeschlagen. Das vollständige Image ist erhalten. ${status.error || ""}`;
        renderUsbFiles(status.files, usbJobId, activeDownloadToken);
      }
      $("usb-eject").hidden = false;
      activeJobId = null;
      updateDestination();
      return;
    } else if (status.state === "wird vorbereitet") {
      $("backup-progress-title").textContent = "USB-Ziel wird geprüft";
      $("backup-progress-value").textContent = "—";
      $("backup-progress-bar").removeAttribute("value");
    }
    transferPoll = window.setTimeout(pollTransfer, 1500);
  } catch (error) {
    $("backup-progress-title").textContent = "USB-Status nicht erreichbar";
    $("backup-progress-detail").textContent = `${error.message} · erneuter Versuch in 3 Sekunden.`;
    transferPoll = window.setTimeout(pollTransfer, 3000);
  }
}

function renderUsbFiles(files, jobId, token) {
  const container = $("usb-results");
  container.replaceChildren();
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "usb-result";
    const copy = document.createElement("div");
    copy.className = "usb-result-copy";
    const title = document.createElement("b");
    title.textContent = file.kind === "geshrinkt" ? "Verkleinertes Image" : "Vollständiges Image";
    const details = document.createElement("small");
    details.textContent = `${file.filename} · ${formatBytes(file.size_bytes)} · SHA-256 ${file.sha256}`;
    copy.append(title, details);
    const link = document.createElement("a");
    link.className = "download-link";
    link.href = `/api/backup/usb/download/${encodeURIComponent(jobId)}/${encodeURIComponent(file.filename)}?token=${encodeURIComponent(token)}`;
    link.download = file.filename;
    link.textContent = "Auf PC herunterladen";
    row.append(copy, link);
    container.append(row);
  }
  container.hidden = files.length === 0;
}

$("usb-eject").addEventListener("click", async () => {
  $("usb-eject").disabled = true;
  try {
    await api("/api/backup/usb/eject", { method: "POST", body: JSON.stringify({}) });
    $("backup-progress-detail").textContent = "USB-Stick sicher ausgeworfen. Du kannst ihn jetzt am PC, Mac oder Linux-Rechner anschließen.";
    $("usb-eject").textContent = "USB-Stick ausgeworfen";
  } catch (error) {
    $("backup-progress-detail").textContent = error.message;
    $("usb-eject").disabled = false;
  }
});

loadDashboard();
pollSystemMetrics();
window.setInterval(() => {
  if (document.visibilityState === "visible") loadUsbDevices();
}, 3000);

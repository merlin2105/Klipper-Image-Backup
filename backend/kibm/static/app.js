const $ = (id) => document.getElementById(id);
const formatLocale = () => window.KIBM_I18N?.formatLocale || "de-DE";
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
let usbImages = [];
let imageScanInProgress = false;
let pendingDeleteImageId = null;
let updatePoll = null;
let nasSettings = { configured: false };
let nasStatus = { state: "idle" };
let nasEditing = false;
let nasBrowsePath = "";
let nasImages = [];
let nasImagesLoaded = false;
const UPDATE_TARGET_KEY = "kibm-update-target";
// An update may create a new HTTPS certificate (e.g. after the Pi's IP changed). The
// open page then cannot reach KIBM until it is reloaded and the certificate accepted.
const UPDATE_UNREACHABLE_MS = 90 * 1000;
let updateUnreachableSince = 0;

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method === "POST" && csrfToken) headers.set("X-KIBM-CSRF", csrfToken);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail || `HTTP ${response.status}`;
    const error = new Error(window.KIBM_I18N?.translate(detail) || detail);
    error.status = response.status;
    throw error;
  }
  return body;
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toLocaleString(formatLocale(), { maximumFractionDigits: 1 })} ${units[unit]}`;
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
  const number = value => value.toLocaleString(formatLocale(), { maximumFractionDigits: 1 });
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
    $("system-uptime").textContent = window.KIBM_I18N?.language === "en"
      ? (days ? `${days} d ${hours} h` : `${hours} h ${minutes} min`)
      : (days ? `${days} T ${hours} Std` : `${hours} Std ${minutes} Min`);
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
    refreshSdHealth();
    renderPrinter(printer);
    renderStorage(storage);
    renderReadiness(currentReadiness);
    if (session.authenticated) await resumeUsbJob();
    await loadNasSettings();
    if (session.authenticated && nasSettings.configured && !nasImagesLoaded) loadNasImages();
    await loadUsbDevices();
    updateDestination();
    refreshUpdateStatus();
    $("footer-time").textContent = `AKTUALISIERT ${new Date().toLocaleTimeString(formatLocale(), { hour: "2-digit", minute: "2-digit" })}`;
  } catch (error) {
    showError(error.message);
  }
}

// Warns only; a backup of a failing card can still be worth trying.
async function refreshSdHealth() {
  try {
    const health = await api("/api/system/sd-health");
    if (!health.kernel_errors && !health.filesystem_errors) return;
    $("overall-led").className = "status-led warning";
    $("overall-title").textContent = "System-microSD meldet Fehler";
    $("overall-description").textContent = `Kernel-Log seit dem Start: ${health.kernel_errors} Kartenfehler · Dateisystem: ${health.filesystem_errors} gespeicherte Fehler. Die Karte ist wahrscheinlich defekt: Ersetze sie bald (siehe README).`;
    $("overall-description").title = health.last_kernel_error || "";
  } catch {
    // The health check is optional; the dashboard works without it.
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
    if (!usbDevices.length) renderExistingImages([]);
    else loadExistingImages();
    updateDestination();
  }
  usbScanInProgress = false;
}

function allImages() {
  return [...usbImages, ...nasImages];
}

function renderExistingImages(images = usbImages) {
  const select = $("existing-image");
  const choices = $("existing-image-choices");
  const selected = select.value;
  usbImages = images;
  const listed = allImages();
  select.replaceChildren(new Option("Image auswählen", ""));
  choices.replaceChildren();
  for (const image of listed) {
    const status = image.shrinkability?.status === "shrinkable"
      ? `noch ca. ${formatBytes(image.shrinkability.estimated_savings_bytes)} verkleinerbar`
      : image.shrinkability?.status === "already_compact" ? "bereits kompakt" : "nicht sicher prüfbar";
    select.append(new Option(`${image.filename} · ${formatBytes(image.size_bytes)} · ${status}`, image.id));
    const choice = document.createElement("button");
    choice.type = "button";
    choice.className = "existing-image-choice";
    choice.dataset.imageId = image.id;
    choice.setAttribute("aria-pressed", String(image.id === selected));
    choice.setAttribute("aria-label", `${image.filename}, ${formatBytes(image.size_bytes)}, ${status}`);
    const filename = document.createElement("strong");
    filename.textContent = image.filename;
    const size = document.createElement("small");
    size.textContent = `${image.location === "nas" ? "NAS" : "USB-Stick"} · ${formatBytes(image.size_bytes)}`;
    const analysis = document.createElement("span");
    analysis.textContent = status;
    choice.append(filename, size, analysis);
    choice.addEventListener("click", () => {
      select.value = image.id;
      choices.querySelectorAll(".existing-image-choice").forEach((item) => {
        item.setAttribute("aria-pressed", String(item === choice));
      });
      updateDestination();
    });
    choices.append(choice);
  }
  if (listed.some(image => image.id === selected)) select.value = selected;
  if (destination === "existing" && !listed.length && !activeJobId) destination = null;
  updateDestination();
}

function scheduleUpdatePoll(delay = 3000) {
  window.clearTimeout(updatePoll);
  updatePoll = window.setTimeout(refreshUpdateStatus, delay);
}

function setUpdateProgress(state, note = "") {
  const dialog = $("update-progress-dialog");
  const running = state === "running" || state === "restarting";
  dialog.dataset.state = state;
  $("update-progress-spinner").hidden = !running;
  $("update-progress-success").hidden = state !== "success";
  $("update-progress-failure").hidden = state !== "failed";
  $("update-progress-track").hidden = !running;
  $("update-progress-close").hidden = state !== "failed";
  $("update-progress-reload").hidden = state !== "success" && state !== "unreachable";
  $("update-progress-title").textContent = state === "success"
    ? "Update erfolgreich installiert"
    : state === "unreachable" ? "Update wahrscheinlich abgeschlossen"
    : state === "failed" ? "Update fehlgeschlagen" : "KIBM-Update läuft";
  $("update-progress-detail").textContent = state === "success"
    ? "Die neue Version ist installiert und KIBM meldet wieder Bereitschaft. Lade die Seite jetzt neu."
    : state === "unreachable"
      ? "Diese Seite erreicht KIBM seit über einer Minute nicht mehr. Nach einem Update kann KIBM ein neues Sicherheitszertifikat erstellen, das der Browser erst nach dem Neuladen annimmt."
    : state === "failed"
      ? "Der Installer hat das Update nicht abgeschlossen. Öffne die Logs zur Fehlerdiagnose."
      : state === "restarting"
        ? "Die KIBM-Dienste starten neu. Die Verbindung kann kurz unterbrochen sein."
        : "Die bestätigte GitHub-Version wird geladen, geprüft und installiert.";
  $("update-progress-note").textContent = note || (state === "running"
    ? "Installation läuft. Bitte dieses Fenster geöffnet lassen."
    : state === "restarting" ? "Warte auf die Bereitschaftsmeldung des Pis …"
    : state === "unreachable" ? "Lade die Seite neu und bestätige die Zertifikatswarnung bei Bedarf einmal. Lädt sie nicht, öffne KIBM über die aktuelle IP-Adresse des Pis." : "");
}

function openUpdateProgress(targetCommit) {
  if (targetCommit) sessionStorage.setItem(UPDATE_TARGET_KEY, targetCommit);
  $("dashboard-content").setAttribute("aria-busy", "true");
  const dialog = $("update-progress-dialog");
  if (!dialog.open) dialog.showModal();
  setUpdateProgress("running");
}

function closeUpdateProgress() {
  sessionStorage.removeItem(UPDATE_TARGET_KEY);
  $("dashboard-content").removeAttribute("aria-busy");
  $("update-progress-dialog").close();
}

async function refreshUpdateStatus(forceRefresh = false) {
  const panel = $("update-panel");
  if (panel.hidden) {
    panel.hidden = false;
    $("update-title").textContent = "Versionsprüfung läuft";
    $("update-detail").textContent = `Installiert ${installedVersionLabel()} · GitHub-Version wird geprüft. Beim Pi-Start kann die erste Prüfung länger dauern.`;
  }
  try {
    const status = await api(forceRefresh ? "/api/update?refresh=true" : "/api/update", { signal: AbortSignal.timeout(30000) });
    updateUnreachableSince = 0;
    const worker = status.worker || {};
    const button = $("update-button");
    const checkButton = $("check-update-button");
    panel.hidden = false;
    button.hidden = !status.can_update || worker.state === "running";
    button.disabled = worker.state === "running";
    checkButton.hidden = worker.state === "running";
    let targetCommit = sessionStorage.getItem(UPDATE_TARGET_KEY);
    if (!targetCommit && worker.state === "running" && status.latest_commit?.sha) {
      targetCommit = status.latest_commit.sha;
      openUpdateProgress(targetCommit);
    }
    if (targetCommit) {
      openUpdateProgress(targetCommit);
      if (worker.state === "failed") {
        sessionStorage.removeItem(UPDATE_TARGET_KEY);
        $("dashboard-content").removeAttribute("aria-busy");
        setUpdateProgress("failed", `Installer-Status ${worker.exit_status || "unbekannt"}. Logs: sudo kibm-admin logs.`);
      } else if (status.current_commit === targetCommit && worker.state !== "running") {
        setUpdateProgress("success", `Installiert: ${status.current_version || installedVersionLabel()}.`);
      } else {
        const restarting = worker.state !== "running";
        setUpdateProgress(restarting ? "restarting" : "running", restarting
          ? "Der Pi arbeitet weiter; die Oberfläche wartet auf den Abschluss."
          : "GitHub-Version wird geladen und installiert. Bitte Pi eingeschaltet lassen.");
        scheduleUpdatePoll(2500);
      }
      return;
    }
    if (worker.state === "running") {
      $("update-title").textContent = "Update wird installiert";
      $("update-detail").textContent = "KIBM lädt die GitHub-Version und startet die Dienste neu. Diese Seite kann kurz nicht erreichbar sein.";
      scheduleUpdatePoll();
    } else if (worker.state === "failed") {
      $("update-title").textContent = "Letztes Update fehlgeschlagen";
      $("update-detail").textContent = `Installiert ${status.current_version || installedVersionLabel()} · Installer-Status ${worker.exit_status || "unbekannt"}. Logs: sudo kibm-admin logs.`;
    } else if (status.check_error) {
      $("update-title").textContent = "GitHub-Version derzeit nicht erreichbar";
      $("update-detail").textContent = `Installiert ${status.current_version || installedVersionLabel()} · ${status.check_error} Erneuter Versuch in 15 Sekunden.`;
      button.hidden = true;
      window.clearTimeout(updatePoll);
      updatePoll = window.setTimeout(refreshUpdateStatus, 15000);
    } else if (status.update_available && !status.can_update) {
      $("update-title").textContent = "Update vorhanden · Web-Updater nicht bereit";
      $("update-detail").textContent = `Installiert ${status.current_version || installedVersionLabel()} · verfügbar ${status.latest_version || "Versionsnummer nicht abrufbar"}. Diese Installation muss einmalig per SSH mit sudo kibm-admin update vorbereitet werden.`;
    } else if (status.update_available) {
      $("update-title").textContent = "Update vorhanden";
      const current = status.current_version || installedVersionLabel();
      const latest = status.latest_version || "Versionsnummer nicht abrufbar";
      const dirty = status.current_dirty ? " · lokale Änderungen erkannt" : "";
      $("update-detail").textContent = `${current} → ${latest}${dirty} · ${status.latest_commit.message}`;
    } else {
      $("update-title").textContent = "KIBM ist aktuell";
      $("update-detail").textContent = `Installiert ${status.current_version || installedVersionLabel()} · GitHub ${status.latest_version || "Versionsnummer nicht abrufbar"}.`;
    }
  } catch {
    const targetCommit = sessionStorage.getItem(UPDATE_TARGET_KEY);
    if (targetCommit) {
      openUpdateProgress(targetCommit);
      if (!updateUnreachableSince) updateUnreachableSince = Date.now();
      if (Date.now() - updateUnreachableSince >= UPDATE_UNREACHABLE_MS) {
        setUpdateProgress("unreachable");
      } else {
        setUpdateProgress("restarting", "KIBM startet seine Dienste neu. Die Verbindung wird automatisch wieder geprüft.");
      }
      scheduleUpdatePoll(3000);
      return;
    }
    $("update-panel").hidden = false;
    $("update-button").hidden = true;
    $("update-title").textContent = "Versionsprüfung dauert noch";
    $("update-detail").textContent = `Installiert ${installedVersionLabel()} · KIBM oder GitHub antwortet noch nicht. Automatischer erneuter Versuch in 10 Sekunden.`;
    $("check-update-button").hidden = false;
    scheduleUpdatePoll(10000);
  }
}

function installedVersionLabel() {
  return `v${$("kibm-version").textContent.trim()} Beta`;
}

async function loadExistingImages() {
  if (imageScanInProgress || activeJobId || !usbDevices.length) return;
  imageScanInProgress = true;
  try {
    const { images } = await api("/api/backup/usb/images", { signal: AbortSignal.timeout(12000) });
    if (JSON.stringify(images) !== JSON.stringify(usbImages)) renderExistingImages(images);
  } catch {
    if (usbImages.length) renderExistingImages([]);
  } finally {
    imageScanInProgress = false;
  }
}

function renderUsbDetection() {
  const device = selectedUsbDevice();
  let detail = "";
  if (usbDeviceSignature === "unavailable") {
    detail = "USB-Erkennung nicht erreichbar. KIBM versucht es automatisch erneut.";
  } else if (device) {
    detail = `${device.model?.trim() || "USB-Stick"} · ${device.path} · ${formatBytes(device.size_bytes)}. Vor der Sicherung wird der Stick als exFAT formatiert; vorhandene Daten werden gelöscht.`;
  } else if (usbDevices.length) {
    detail = `${usbDevices.length === 1 ? "USB-Stick erkannt" : `${usbDevices.length} USB-Sticks erkannt`}. Wähle den gewünschten Stick aus.`;
  }
  const description = $("usb-detection-detail");
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
  renderNas();
  const usbMode = isUsbDestination();
  const existingMode = destination === "existing";
  const nasMode = destination === "nas";
  const existingImage = allImages().find(image => image.id === $("existing-image").value);
  const existingCanShrink = existingImage?.shrinkability?.status === "shrinkable";
  const device = selectedUsbDevice();
  const shrink = $("shrink-image").checked;
  const running = Boolean(activeJobId);
  $("destination-pc").disabled = running;
  $("destination-usb").disabled = running || usbDevices.length === 0;
  $("destination-existing").disabled = running;
  $("destination-existing").hidden = !allImages().length;
  $("destination-nas").disabled = running;
  $("destination-nas").setAttribute("aria-pressed", String(nasMode));
  $("nas-options").hidden = !nasMode;
  const nasReady = nasMode && nasStatus.state === "ok" && !nasEditing;
  $("image-options").hidden = !(usbMode || nasReady);
  $("reload-nas-images").hidden = !(csrfToken && nasSettings.configured);
  $("destination-existing").setAttribute("aria-pressed", String(existingMode));
  $("destination-pc").setAttribute("aria-pressed", String(destination === "pc"));
  $("destination-usb").setAttribute("aria-pressed", String(usbMode));
  $("pc-options").hidden = destination !== "pc";
  $("usb-options").hidden = !usbMode;
  $("existing-options").hidden = !existingMode;
  $("keep-full-row").hidden = !shrink;
  $("backup-confirmations").hidden = !destination || (nasMode && !nasReady);
  $("format-confirm-row").hidden = !usbMode || !device;
  $("start-backup").hidden = !destination || (nasMode && !nasReady);
  $("start-backup").textContent = existingMode ? "Image verkleinern" : "Image erstellen";
  const ready = existingMode
    ? Boolean(readiness && readiness.printer?.classification !== "active")
    : Boolean(readiness?.backup_available);
  $("existing-image-analysis").textContent = !existingImage
    ? "KIBM prüft Dateisystem und Partitionsgrößen unabhängig vom Dateinamen."
    : existingCanShrink
      ? `KIBM erkennt etwa ${formatBytes(existingImage.shrinkability.estimated_savings_bytes)} möglichen Shrink-Spielraum. Die tatsächliche Ersparnis kann geringer sein; das Original bleibt erhalten.`
      : existingImage.shrinkability?.status === "already_compact"
        ? "Das Image ist bereits kompakt oder bietet weniger als 256 MB geschätzten Shrink-Spielraum. Ein erneuter Shrink wird nicht angeboten."
        : "Das Dateisystem konnte nicht sicher geprüft werden. KIBM bietet deshalb keinen Shrink an.";
  $("delete-existing-image").disabled = !existingImage || running;
  $("existing-image-choices").querySelectorAll(".existing-image-choice").forEach((choice) => { choice.disabled = running; });
  const minimumUsbBytes = requiredUsbCapacity(shrink);
  const targetTooSmall = Boolean(usbMode && device && minimumUsbBytes && device.size_bytes < minimumUsbBytes);
  const minimumNasBytes = requiredUsbCapacity(shrink) - (minimumUsbBytes ? USB_FORMAT_HEADROOM_BYTES : 0);
  const nasTooSmall = Boolean(nasMode && nasStatus.state === "ok" && minimumNasBytes && nasStatus.free < minimumNasBytes);
  $("start-backup").disabled = !destination || !ready || running || !$("backup-confirm").checked
    || (existingMode && (!$("existing-image").value || !existingCanShrink))
    || (usbMode && (!device || targetTooSmall || !$("format-confirm").checked))
    || (nasMode && (nasStatus.state !== "ok" || nasTooSmall));
  let capacityMessage = "";
  if (targetTooSmall) {
    const gib = bytes => `${(bytes / 1024 ** 3).toLocaleString(formatLocale(), { maximumFractionDigits: 2 })} GiB`;
    capacityMessage = `Zu wenig Platz: Der Stick hat ${gib(device.size_bytes)}. Diese Auswahl benötigt rund ${gib(minimumUsbBytes)}.`;
    if (shrink) capacityMessage += " PiShrink braucht während der Verarbeitung Platz für zwei vollständige Kopien plus Reserve.";
    capacityMessage += shrink && device.size_bytes >= requiredUsbCapacity(false)
      ? " Entferne den Haken bei „Image … verkleinern“, um das vollständige Image auf diesem Stick zu sichern."
      : " Wähle einen größeren Stick oder sichere das Image auf dem PC.";
  }
  if (nasTooSmall) {
    const gib = bytes => `${(bytes / 1024 ** 3).toLocaleString(formatLocale(), { maximumFractionDigits: 2 })} GiB`;
    capacityMessage = `Zu wenig Platz auf dem NAS: frei ${gib(nasStatus.free)}, benötigt rund ${gib(minimumNasBytes)}.`;
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
  const state = (printer.state || "UNBEKANNT").toLowerCase();
  const states = window.KIBM_I18N?.language === "en"
    ? { idle: "IDLE", standby: "STANDBY", printing: "PRINTING", paused: "PAUSED", complete: "COMPLETE", cancelled: "CANCELLED", error: "ERROR", unknown: "UNKNOWN" }
    : { idle: "LEERLAUF", standby: "STANDBY", printing: "DRUCKT", paused: "PAUSIERT", complete: "ABGESCHLOSSEN", cancelled: "ABGEBROCHEN", error: "FEHLER", unknown: "UNBEKANNT" };
  $("printer-state").textContent = states[state] || state.toUpperCase();
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

$("check-update-button").addEventListener("click", async () => {
  const button = $("check-update-button");
  button.disabled = true;
  button.textContent = "Prüfe GitHub …";
  $("update-title").textContent = "GitHub-Version wird geprüft …";
  $("update-detail").textContent = "KIBM fragt den aktuellen Stand von GitHub ab.";
  window.clearTimeout(updatePoll);
  try {
    await refreshUpdateStatus(true);
  } finally {
    button.disabled = false;
    button.textContent = "Auf Update prüfen";
  }
});

for (const target of ["pc", "usb", "nas", "existing"]) {
  $(`destination-${target}`).addEventListener("click", () => {
    if (destination !== target) {
      destination = target;
      $("backup-confirm").checked = false;
      $("format-confirm").checked = false;
      if (target === "nas" && csrfToken && nasSettings.configured && !nasEditing) checkNas();
    }
    renderUsbDetection();
    renderNas();
    updateDestination();
  });
}
$("backup-confirm").addEventListener("change", updateDestination);
$("format-confirm").addEventListener("change", updateDestination);
$("existing-image").addEventListener("change", updateDestination);
$("delete-existing-image").addEventListener("click", async () => {
  const image = allImages().find(item => item.id === $("existing-image").value);
  if (!image) return;
  pendingDeleteImageId = image.id;
  $("delete-image-detail").textContent = `${image.filename} · ${formatBytes(image.size_bytes)}`;
  $("delete-image-error").textContent = "";
  $("delete-image-password").value = "";
  $("delete-image-confirm").disabled = true;
  $("delete-image-dialog").showModal();
  try {
    const session = await api("/api/session");
    csrfToken = session.authenticated ? session.csrf_token : "";
    $("delete-image-auth-fields").hidden = session.authenticated;
    $("delete-image-password").required = !session.authenticated;
    if (!session.authenticated) $("delete-image-password").focus();
  } catch (error) {
    $("delete-image-error").textContent = error.message;
  } finally {
    $("delete-image-confirm").disabled = false;
  }
});

$("delete-image-cancel").addEventListener("click", () => $("delete-image-dialog").close());
$("delete-image-confirm").addEventListener("click", async () => {
  const image = allImages().find(item => item.id === pendingDeleteImageId);
  if (!image) {
    $("delete-image-error").textContent = "Das ausgewählte Image ist nicht mehr verfügbar. Scanne den Stick erneut.";
    return;
  }
  const button = $("delete-image-confirm");
  button.disabled = true;
  $("delete-image-error").textContent = "";
  try {
    if (!csrfToken) {
      if (!$("delete-image-password").value) {
        $("delete-image-error").textContent = "Gib zuerst dein bestehendes Pi-Passwort ein.";
        $("delete-image-password").focus();
        return;
      }
      const login = await api("/api/login", {
        method: "POST",
        body: JSON.stringify({ password: $("delete-image-password").value }),
      });
      csrfToken = login.csrf_token;
      $("delete-image-password").value = "";
    }
    await api("/api/backup/usb/images/delete", { method: "POST", body: JSON.stringify({ image_id: image.id, confirmed: true }) });
    $("delete-image-dialog").close();
    pendingDeleteImageId = null;
    if (image.location === "nas") {
      await loadNasImages();
      $("preflight-message").textContent = `Image „${image.filename}“ wurde vom NAS gelöscht.`;
    } else {
      await loadExistingImages();
      $("preflight-message").textContent = `Image „${image.filename}“ wurde vom USB-Stick gelöscht.`;
    }
  } catch (error) {
    $("delete-image-error").textContent = error.message;
    if (error.status === 401) {
      csrfToken = "";
      $("delete-image-auth-fields").hidden = false;
      $("delete-image-password").required = true;
      $("delete-image-password").value = "";
      $("delete-image-password").focus();
    }
  } finally {
    button.disabled = false;
  }
});
$("usb-device").addEventListener("change", () => {
  $("format-confirm").checked = false;
  renderUsbDetection();
  updateDestination();
});
$("shrink-image").addEventListener("change", updateDestination);
$("refresh-usb").addEventListener("click", loadUsbDevices);

$("update-button").addEventListener("click", async () => {
  try {
    const session = await api("/api/session");
    const status = await api("/api/update");
    csrfToken = session.authenticated ? session.csrf_token : "";
    $("update-auth-fields").hidden = session.authenticated;
    $("update-password").required = !session.authenticated;
    $("update-password").value = "";
    $("update-error").textContent = "";
    $("update-eject").hidden = !status.worker?.usb_mounted;
    const current = status.current_version || installedVersionLabel();
    const latest = status.latest_version || "Versionsnummer nicht abrufbar";
    const ejectNotice = status.worker?.usb_mounted ? " Bitte wirf zuerst den USB-Stick sicher aus." : "";
    $("update-dialog-detail").textContent = `${current} → ${latest}.${ejectNotice} KIBM startet seine Dienste während des Updates neu.`;
    $("update-dialog").showModal();
  } catch (error) {
    $("update-detail").textContent = error.message;
  }
});

$("update-cancel").addEventListener("click", () => {
  $("update-password").value = "";
  $("update-dialog").close();
});

$("update-eject").addEventListener("click", async () => {
  const button = $("update-eject");
  button.disabled = true;
  $("update-error").textContent = "";
  try {
    if (!csrfToken) {
      const password = $("update-password").value;
      if (!password) {
        $("update-error").textContent = "Gib zuerst dein bestehendes Pi-Passwort ein.";
        $("update-password").focus();
        return;
      }
      const login = await api("/api/login", { method: "POST", body: JSON.stringify({ password }) });
      csrfToken = login.csrf_token;
      $("update-password").value = "";
      $("update-auth-fields").hidden = true;
    }
    await api("/api/backup/usb/eject", { method: "POST", body: JSON.stringify({}), signal: AbortSignal.timeout(45000) });
    button.hidden = true;
    $("update-dialog-detail").textContent = "USB-Stick sicher ausgeworfen. Du kannst das Update jetzt starten.";
  } catch (error) {
    $("update-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("update-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = $("update-submit");
  submit.disabled = true;
  $("update-error").textContent = "";
  let progressOpened = false;
  try {
    if (!csrfToken) {
      const login = await api("/api/login", {
        method: "POST",
        body: JSON.stringify({ password: $("update-password").value }),
      });
      csrfToken = login.csrf_token;
      $("update-password").value = "";
      $("update-auth-fields").hidden = true;
    }
    const status = await api("/api/update");
    if (!status.can_update || !status.latest_commit?.sha) throw new Error("Es ist kein startbereites Update verfügbar. Prüfe den Status erneut.");
    $("update-dialog").close();
    openUpdateProgress(status.latest_commit.sha);
    progressOpened = true;
    try {
      await api("/api/update", { method: "POST", body: JSON.stringify({ confirmed: true }) });
    } catch (error) {
      if (!error.status) {
        setUpdateProgress("restarting", "Der Pi hat den Start möglicherweise bereits angenommen. Ich prüfe den Update-Status weiter.");
        scheduleUpdatePoll(1500);
        return;
      }
      throw error;
    }
    setUpdateProgress("running", "Der Pi hat das Update angenommen. Bitte eingeschaltet lassen.");
    $("update-button").hidden = true;
    scheduleUpdatePoll(1500);
  } catch (error) {
    if (progressOpened) {
      sessionStorage.removeItem(UPDATE_TARGET_KEY);
      $("dashboard-content").removeAttribute("aria-busy");
      $("update-progress-dialog").close();
      $("update-dialog").showModal();
    }
    $("update-error").textContent = error.message;
    if (/USB-Stick.*auswerfen|safely eject the USB drive/i.test(error.message)) $("update-eject").hidden = false;
  } finally {
    submit.disabled = false;
  }
});

$("update-progress-dialog").addEventListener("cancel", (event) => {
  if (sessionStorage.getItem(UPDATE_TARGET_KEY)) event.preventDefault();
});

$("update-progress-close").addEventListener("click", () => {
  closeUpdateProgress();
  refreshUpdateStatus();
});

$("update-progress-reload").addEventListener("click", () => {
  closeUpdateProgress();
  window.location.reload();
});

$("start-backup").addEventListener("click", async () => {
  try {
    const session = await api("/api/session");
    if ($("start-backup").disabled || $("backup-login").open) return;
    csrfToken = session.authenticated ? session.csrf_token : "";
    $("pi-auth-fields").hidden = session.authenticated;
    $("backup-dialog-title").textContent = session.authenticated ? "Image-Erstellung bestätigen" : "Mit deinem Pi-Konto anmelden";
    $("dialog-submit-label").textContent = isUsbDestination() ? "USB-Image erstellen" : destination === "nas" ? "NAS-Image erstellen" : "Image herunterladen";
    $("backup-login-error").textContent = "";
    $("pi-password").required = !session.authenticated;
    $("pi-password").value = "";
    if (destination === "existing") {
      $("dialog-submit-label").textContent = "Image verkleinern";
      const image = allImages().find(item => item.id === $("existing-image").value);
      $("backup-dialog-detail").textContent = `${image.filename} · Verkleinerte Kopie auf ${image.location === "nas" ? nasSettings.display : image.partition}.`;
    } else if (destination === "nas") {
      $("backup-dialog-detail").textContent = `Ziel: ${nasSettings.display}. Auf dem NAS wird nichts formatiert.`;
    } else if (isUsbDestination()) {
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
    if (destination === "existing") {
      result = await api("/api/backup/usb/shrink", {
        method: "POST",
        body: JSON.stringify({ image_id: $("existing-image").value, confirmed: $("backup-confirm").checked }),
      });
      activeJobType = "usb";
      activeDownloadToken = result.download_token;
      usbJobId = result.job_id;
    } else if (destination === "nas") {
      result = await api("/api/backup/nas/start", {
        method: "POST",
        body: JSON.stringify({
          shrink: $("shrink-image").checked,
          keep_full: $("keep-full-image").checked,
          verify_image: $("verify-image").checked,
          confirmed: $("backup-confirm").checked,
        }),
        signal: AbortSignal.timeout(130000),
      });
      activeJobType = "usb";
      activeDownloadToken = result.download_token;
      usbJobId = result.job_id;
    } else if (isUsbDestination()) {
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
    $("backup-progress-title").textContent = result.target === "nas" ? "NAS-Auftrag gestartet" : activeJobType === "usb" ? "USB-Auftrag gestartet" : "Download bereit";
    $("backup-progress-value").textContent = "0 %";
    $("backup-progress-bar").value = 0;
    $("backup-progress-bar").setAttribute("value", "0");
    $("backup-progress-detail").textContent = result.target === "nas"
      ? `NAS ${result.device.path} · ${result.message}`
      : activeJobType === "usb"
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
    if (error.status === 401) csrfToken = "";
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
    if (["fehler", "abgebrochen", "abgelaufen"].includes(status.state)) {
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
    if (error.status === 401) {
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

let usbJobResumeChecked = false;
async function resumeUsbJob() {
  // After a reload or service restart, pick up the media worker's job again once.
  if (usbJobResumeChecked || activeJobId || usbJobId) return;
  usbJobResumeChecked = true;
  try {
    const { job } = await api("/api/backup/usb/current");
    if (!job) return;
    activeJobType = "usb";
    activeDownloadToken = job.download_token;
    usbJobId = job.job_id;
    activeJobId = job.job_id;
    $("backup-progress").hidden = false;
    $("backup-download").hidden = true;
    $("backup-progress-title").textContent = job.target === "nas" ? "NAS-Auftrag gefunden" : "USB-Auftrag gefunden";
    $("backup-progress-detail").textContent = job.error || job.message;
    pollTransfer();
  } catch {
    // The dashboard stays usable without the resume check.
  }
}

async function pollUsbJob() {
  if (!activeJobId) return;
  try {
    const status = await api(`/api/backup/usb/jobs/${activeJobId}`);
    const onNas = status.target === "nas";
    $("backup-progress-detail").textContent = status.error || status.message;
    const cancel = $("job-cancel");
    cancel.hidden = !RUNNING_JOB_STATES.includes(status.state) || !csrfToken;
    cancel.disabled = Boolean(status.cancel_requested);
    cancel.textContent = status.cancel_requested ? "Wird abgebrochen …" : "Vorgang abbrechen";
    if (status.state === "sichern") {
      const percent = status.image_size_bytes ? Math.min(100, status.sent_bytes / status.image_size_bytes * 100) : 0;
      $("backup-progress-title").textContent = onNas ? "Vollständiges Image wird auf das NAS gesichert" : "Vollständiges Image wird auf USB gesichert";
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
      $("backup-progress-title").textContent = onNas ? "Image auf dem NAS wird geprüft" : "Image auf dem USB-Stick wird geprüft";
      showMeasuredProgress(status.verified_bytes, status.image_size_bytes);
      $("backup-progress-detail").textContent = `${formatBytes(status.verified_bytes)} von ${formatBytes(status.image_size_bytes)} für SHA-256 gelesen.`;
    } else if (status.state === "reparieren") {
      $("backup-progress-title").textContent = "Dateisystem im Image wird repariert";
      if (status.phase === "pruefsumme") {
        showMeasuredProgress(status.verified_bytes, status.image_size_bytes);
        $("backup-progress-detail").textContent = `${status.message} ${formatBytes(status.verified_bytes)} von ${formatBytes(status.image_size_bytes)}.`;
      } else {
        $("backup-progress-value").textContent = "e2fsck";
        $("backup-progress-bar").removeAttribute("value");
      }
    } else if (status.state === "formatieren") {
      $("backup-progress-title").textContent = "USB-Stick wird vorbereitet";
      $("backup-progress-value").textContent = "exFAT";
      $("backup-progress-bar").removeAttribute("value");
    } else if (status.state === "bereit") {
      $("backup-progress-title").textContent = onNas ? "Image auf dem NAS gespeichert" : "Image auf USB bereit";
      $("backup-progress-value").textContent = "Fertig";
      $("backup-progress-bar").setAttribute("value", "100");
      $("backup-progress-bar").value = 100;
      renderUsbFiles(status.files || [], usbJobId, activeDownloadToken, onNas);
      $("usb-eject").hidden = onNas;
      if (onNas) loadNasImages();
      activeJobId = null;
      updateDestination();
      return;
    } else if (status.state === "fehler") {
      $("backup-progress-title").textContent = destination === "existing" ? "Verkleinern fehlgeschlagen" : onNas ? "NAS-Sicherung fehlgeschlagen" : "USB-Sicherung fehlgeschlagen";
      $("backup-progress-value").textContent = "Fehler";
      $("backup-progress-bar").setAttribute("value", "0");
      $("backup-progress-bar").value = 0;
      if ((status.files || []).length) {
        $("backup-progress-detail").textContent = `Der Shrink ist fehlgeschlagen. Das vollständige Image ist erhalten. ${status.error || ""}`;
        renderUsbFiles(status.files, usbJobId, activeDownloadToken, onNas);
      }
      $("usb-eject").hidden = onNas;
      if (onNas) loadNasImages();
      activeJobId = null;
      updateDestination();
      return;
    } else if (status.state === "abgebrochen") {
      $("backup-progress-title").textContent = destination === "existing" ? "Verkleinern abgebrochen" : onNas ? "NAS-Sicherung abgebrochen" : "USB-Sicherung abgebrochen";
      $("backup-progress-value").textContent = "Abgebrochen";
      $("backup-progress-bar").setAttribute("value", "0");
      $("backup-progress-bar").value = 0;
      if ((status.files || []).length) renderUsbFiles(status.files, usbJobId, activeDownloadToken, onNas);
      $("usb-eject").hidden = onNas;
      if (onNas) loadNasImages();
      activeJobId = null;
      updateDestination();
      return;
    } else if (status.state === "wird vorbereitet") {
      $("backup-progress-title").textContent = onNas ? "NAS-Verbindung wird geprüft" : "USB-Ziel wird geprüft";
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

const CANCEL_QUESTION = "Laufenden Vorgang abbrechen? Unvollständige Dateien werden gelöscht, vorhandene Sicherungen bleiben erhalten.";
const RUNNING_JOB_STATES = ["wird vorbereitet", "formatieren", "sichern", "verifizieren", "reparieren", "schrumpfen"];

$("job-cancel").addEventListener("click", async () => {
  const jobId = activeJobId;
  if (!jobId || !window.confirm(window.KIBM_I18N?.translate(CANCEL_QUESTION) || CANCEL_QUESTION)) return;
  const button = $("job-cancel");
  button.disabled = true;
  try {
    await api(`/api/backup/usb/jobs/${jobId}/cancel`, { method: "POST" });
    button.textContent = "Wird abgebrochen …";
  } catch (error) {
    button.disabled = false;
    $("backup-progress-detail").textContent = error.message;
  }
});

function renderUsbFiles(files, jobId, token, onNas = false) {
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
    if (onNas) {
      const where = document.createElement("small");
      where.textContent = `Gespeichert auf ${nasSettings.display || "dem NAS"}`;
      copy.append(where);
      row.append(copy);
      container.append(row);
      continue;
    }
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

window.addEventListener("kibm:languagechange", async () => {
  const selectedPath = $("usb-device").value;
  $("usb-device").replaceChildren(new Option("USB-Stick auswählen", ""));
  for (const device of usbDevices) {
    const detail = device.model?.trim() || device.path;
    $("usb-device").append(new Option(`${detail} · ${formatBytes(device.size_bytes)}`, device.path));
  }
  if (usbDevices.some((device) => device.path === selectedPath)) $("usb-device").value = selectedPath;
  renderExistingImages(usbImages);
  renderUsbDetection();
  renderNas();
  await loadDashboard();
  refreshSystemMetrics();
});
const NAS_ADDRESS = /^(?:smb:)?[/\\]{2}([^/\\]+)[/\\]+([^/\\]+)[/\\]*$/i;

async function loadNasSettings() {
  try {
    nasSettings = await api("/api/backup/nas");
  } catch {
    nasSettings = { configured: false };
  }
  renderNas();
}

function renderNas() {
  const loggedIn = Boolean(csrfToken);
  $("destination-nas-detail").textContent = nasSettings.configured
    ? (nasSettings.display || "Verbindung gespeichert")
    : "Auf Netzwerkfreigabe speichern";
  $("nas-login").hidden = loggedIn;
  $("nas-connected").hidden = !loggedIn || !nasSettings.configured || nasEditing;
  $("nas-setup").hidden = !loggedIn || (nasSettings.configured && !nasEditing);
  $("nas-cancel-edit").hidden = !nasEditing;
  $("nas-password-hint").hidden = !nasEditing;
  const status = $("nas-status");
  status.className = `nas-status ${nasStatus.state === "busy" ? "ok" : nasStatus.state}`;
  status.textContent = nasStatus.state === "checking"
    ? "Verbindung wird geprüft …"
    : nasStatus.state === "ok"
      ? `✓ Verbindung OK · ${nasStatus.display} · ${formatBytes(nasStatus.free)} frei`
      : nasStatus.state === "busy"
        ? `✓ NAS-Sicherung läuft · ${nasStatus.display}`
        : nasStatus.state === "error" ? `✗ ${nasStatus.text}` : "";
}

function nasLoggedOut(error) {
  if (error.status !== 401) return false;
  csrfToken = "";
  renderNas();
  return true;
}

async function checkNas() {
  nasStatus = { state: "checking" };
  renderNas();
  updateDestination();
  try {
    const result = await api("/api/backup/nas/check", { method: "POST", body: "{}", signal: AbortSignal.timeout(130000) });
    nasStatus = result.ok
      ? { state: result.busy ? "busy" : "ok", display: result.display, free: result.free_bytes }
      : { state: "error", text: window.KIBM_I18N?.translate(result.error) || result.error };
  } catch (error) {
    nasStatus = { state: "error", text: error.message };
    nasLoggedOut(error);
  }
  renderNas();
  updateDestination();
}

async function loadNasImages() {
  if (!csrfToken || !nasSettings.configured) {
    nasImages = [];
    renderExistingImages();
    return;
  }
  nasImagesLoaded = true;
  try {
    const result = await api("/api/backup/nas/images", { method: "POST", body: "{}", signal: AbortSignal.timeout(130000) });
    if (!result.busy) nasImages = result.images || [];
  } catch (error) {
    nasImages = [];
    nasLoggedOut(error);
  }
  renderExistingImages();
}

function splitNasAddress() {
  const match = $("nas-server").value.trim().match(NAS_ADDRESS);
  if (match) {
    $("nas-server").value = match[1];
    $("nas-share").value = match[2];
  }
}

function nasForm() {
  splitNasAddress();
  return {
    server: $("nas-server").value.trim(),
    share: $("nas-share").value.trim(),
    username: $("nas-username").value.trim(),
    password: $("nas-password").value,
  };
}

function nasChip(label, detail, onClick) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "nas-chip";
  chip.textContent = label;
  if (detail) {
    const small = document.createElement("small");
    small.textContent = detail;
    chip.append(small);
  }
  chip.addEventListener("click", onClick);
  return chip;
}

async function nasAction(button, action) {
  button.disabled = true;
  $("nas-setup-error").textContent = "";
  try {
    await action();
  } catch (error) {
    $("nas-setup-error").textContent = error.message;
    nasLoggedOut(error);
  } finally {
    button.disabled = false;
  }
}

async function browseNas(path) {
  const result = await api("/api/backup/nas/browse", {
    method: "POST", body: JSON.stringify({ ...nasForm(), path }), signal: AbortSignal.timeout(130000),
  });
  nasBrowsePath = result.path;
  $("nas-browser").hidden = false;
  const crumbs = $("nas-breadcrumb");
  crumbs.replaceChildren();
  const parts = nasBrowsePath ? nasBrowsePath.split("/") : [];
  const crumb = (label, target) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", () => nasAction(button, () => browseNas(target)));
    return button;
  };
  crumbs.append(crumb(`//${nasForm().server}/${nasForm().share}`, ""));
  parts.forEach((part, index) => {
    crumbs.append(document.createTextNode("›"), crumb(part, parts.slice(0, index + 1).join("/")));
  });
  const folders = $("nas-folders");
  folders.replaceChildren(...result.folders.map(name => nasChip(name, "", (event) => {
    nasAction(event.currentTarget, () => browseNas(nasBrowsePath ? `${nasBrowsePath}/${name}` : name));
  })));
}

$("nas-server").addEventListener("change", splitNasAddress);
$("nas-server").addEventListener("paste", () => window.setTimeout(splitNasAddress));
for (const id of ["nas-server", "nas-share", "nas-username", "nas-password"]) {
  $(id).addEventListener("input", () => { $("nas-browser").hidden = true; });
}

$("nas-login-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  $("nas-login-error").textContent = "";
  try {
    const password = $("nas-login-password").value;
    if (!password) throw new Error("Gib zuerst dein bestehendes Pi-Passwort ein.");
    const login = await api("/api/login", { method: "POST", body: JSON.stringify({ password }) });
    csrfToken = login.csrf_token;
    $("nas-login-password").value = "";
    await loadNasSettings();
    if (nasSettings.configured) {
      checkNas();
      loadNasImages();
    }
    updateDestination();
  } catch (error) {
    $("nas-login-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$("nas-login-password").addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("nas-login-button").click();
});

$("nas-discover").addEventListener("click", (event) => nasAction(event.currentTarget, async () => {
  $("nas-discover-status").textContent = "Suche läuft …";
  $("nas-devices").replaceChildren();
  try {
    const { devices } = await api("/api/backup/nas/discover", { method: "POST", body: "{}", signal: AbortSignal.timeout(130000) });
    $("nas-discover-status").textContent = devices.length ? "Gerät anklicken, um es zu übernehmen." : "Kein NAS gefunden. Gib die IP-Adresse manuell ein.";
    $("nas-devices").replaceChildren(...devices.map(device => nasChip(device.name, device.name === device.address ? "" : device.address, () => {
      $("nas-server").value = device.address;
      $("nas-devices").querySelectorAll(".nas-chip").forEach(chip => chip.setAttribute("aria-pressed", "false"));
      $("nas-browser").hidden = true;
    })));
  } catch (error) {
    $("nas-discover-status").textContent = "";
    throw error;
  }
}));

$("nas-list-shares").addEventListener("click", (event) => nasAction(event.currentTarget, async () => {
  const form = nasForm();
  if (!form.server || !form.username) throw new Error("Gib zuerst IP-Adresse oder Name des NAS und den Benutzer ein.");
  const { shares } = await api("/api/backup/nas/shares", {
    method: "POST", body: JSON.stringify({ ...form, share: "" }), signal: AbortSignal.timeout(130000),
  });
  if (!shares.length) throw new Error("Das NAS meldet keine Freigaben für diesen Benutzer.");
  $("nas-shares").replaceChildren(...shares.map(name => nasChip(name, "", () => {
    $("nas-share").value = name;
    $("nas-browser").hidden = true;
  })));
}));

$("nas-connect").addEventListener("click", (event) => nasAction(event.currentTarget, async () => {
  const form = nasForm();
  if (!form.server || !form.share || !form.username) throw new Error("Bitte IP-Adresse oder Name des NAS, Freigabename und Benutzer angeben.");
  if (!form.password && !nasEditing) throw new Error("Bitte das Passwort eingeben.");
  try {
    await browseNas(nasEditing ? nasSettings.folder || "" : "");
  } catch (error) {
    if (!nasEditing || !nasSettings.folder) throw error;
    await browseNas("");
  }
}));

$("nas-mkdir").addEventListener("click", (event) => nasAction(event.currentTarget, async () => {
  const name = $("nas-new-folder").value.trim();
  if (!name) throw new Error("Gib einen Namen für den neuen Ordner ein.");
  const result = await api("/api/backup/nas/mkdir", {
    method: "POST", body: JSON.stringify({ ...nasForm(), path: nasBrowsePath, name }), signal: AbortSignal.timeout(130000),
  });
  $("nas-new-folder").value = "";
  await browseNas(result.path);
}));

$("nas-save").addEventListener("click", (event) => nasAction(event.currentTarget, async () => {
  await api("/api/backup/nas/save", {
    method: "POST", body: JSON.stringify({ ...nasForm(), folder: nasBrowsePath }), signal: AbortSignal.timeout(130000),
  });
  $("nas-password").value = "";
  $("nas-browser").hidden = true;
  $("nas-shares").replaceChildren();
  nasEditing = false;
  await loadNasSettings();
  checkNas();
  loadNasImages();
}));

$("nas-recheck").addEventListener("click", checkNas);

$("nas-edit").addEventListener("click", () => {
  nasEditing = true;
  $("nas-server").value = nasSettings.server || "";
  $("nas-share").value = nasSettings.share || "";
  $("nas-username").value = nasSettings.username || "";
  $("nas-password").value = "";
  $("nas-browser").hidden = true;
  $("nas-setup-error").textContent = "";
  renderNas();
  updateDestination();
});

$("nas-cancel-edit").addEventListener("click", () => {
  nasEditing = false;
  $("nas-password").value = "";
  renderNas();
  updateDestination();
});

$("nas-remove").addEventListener("click", async (event) => {
  const question = `NAS-Verbindung ${nasSettings.display} entfernen? Die Images auf dem NAS bleiben erhalten.`;
  if (!window.confirm(window.KIBM_I18N?.translate(question) || question)) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/backup/nas/delete", { method: "POST", body: "{}" });
    nasStatus = { state: "idle" };
    nasImages = [];
    await loadNasSettings();
    renderExistingImages();
  } catch (error) {
    nasStatus = { state: "error", text: error.message };
    nasLoggedOut(error);
    renderNas();
  } finally {
    button.disabled = false;
    updateDestination();
  }
});

$("reload-nas-images").addEventListener("click", loadNasImages);

loadDashboard();
pollSystemMetrics();
window.setInterval(() => {
  if (document.visibilityState === "visible") loadUsbDevices();
}, 3000);
window.setInterval(() => {
  if (document.visibilityState === "visible") loadExistingImages();
}, 10000);
window.setInterval(() => {
  if (document.visibilityState === "visible") refreshUpdateStatus();
}, 15 * 60 * 1000);

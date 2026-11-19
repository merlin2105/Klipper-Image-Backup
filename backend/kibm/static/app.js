const $ = (id) => document.getElementById(id);
let csrfToken = "";
let readiness = null;
let activeJobId = null;
let transferPoll = null;

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
    $("overall-title").textContent = "Diagnose aktiv · Nur lesend";
    $("overall-description").textContent = currentReadiness.backup_available
      ? "Drucker im Standby · vollständige System-microSD erkannt."
      : currentReadiness.blockers.join(" ");
    $("overall-led").className = currentReadiness.backup_available ? "status-led" : "status-led warning";
    renderPrinter(printer);
    renderStorage(storage);
    renderReadiness(currentReadiness);
    $("footer-time").textContent = `AKTUALISIERT ${new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`;
  } catch (error) {
    showError(error.message);
  }
}

function renderReadiness(result) {
  const checks = result.checks || {};
  setCapability("printer-capability", "Druckerstatus", checks.printer_ready, checks.printer_ready ? "Standby" : "gesperrt");
  setCapability("storage-capability", "System-microSD", checks.system_card_supported, checks.system_card_supported ? "unterstützt" : "nicht unterstützt");
  setCapability("read-capability", "Leserechte", checks.read_access, checks.read_access ? "bereit" : "fehlen");
  const size = result.image_size_bytes;
  $("image-estimate").textContent = size ? `${formatBytes(size)} · vollständige Karte` : "Größe nicht verfügbar";
  $("image-estimate-badge").textContent = result.backup_available ? "BEREIT" : "GESPERRT";
  $("image-estimate-badge").classList.toggle("estimate-ready", result.backup_available);
  $("start-backup").disabled = !result.backup_available || Boolean(activeJobId);
  $("preflight-message").textContent = result.backup_available
    ? "Voraussetzungen erfüllt. Das Image enthält die gesamte Karte und wird nicht verkleinert."
    : result.blockers.join(" ");
}

function setCapability(id, label, enabled, detail) {
  const row = $(id);
  const icon = row.querySelector("i");
  icon.className = enabled ? "cap-yes" : "cap-no";
  icon.replaceChildren();
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", enabled ? "m3 8 3 3 7-7" : "m4 4 8 8m0-8-8 8");
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
  $("restart-safety").textContent = `BACKUP: ${printer.safe_to_backup ? "BEREIT" : "GESPERRT"}`;
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
  } catch (error) { $("preflight-message").textContent = error.message; }
});

$("start-backup").addEventListener("click", async () => {
  try {
    const session = await api("/api/session");
    csrfToken = session.authenticated ? session.csrf_token : "";
    $("pi-auth-fields").hidden = session.authenticated;
    $("backup-dialog-title").textContent = session.authenticated ? "Image-Erstellung bestätigen" : "Mit deinem Pi-Konto anmelden";
    $("backup-login-error").textContent = "";
    $("pi-password").required = !session.authenticated;
    $("pi-password").value = "";
    $("backup-confirm").checked = false;
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
    const result = await api("/api/backup/start", {
      method: "POST",
      body: JSON.stringify({ confirmed: $("backup-confirm").checked }),
    });
    $("backup-login").close();
    activeJobId = result.job_id;
    $("backup-progress").hidden = false;
    $("backup-progress-title").textContent = "Download bereit";
    $("backup-progress-value").textContent = "0 %";
    $("backup-progress-bar").value = 0;
    $("backup-progress-detail").textContent = `${result.filename} · ${formatBytes(result.size_bytes)}. Wähle den Speicherort im Browser.`;
    const download = $("backup-download");
    download.href = result.download_url;
    download.download = result.filename;
    download.hidden = false;
    $("start-backup").disabled = true;
    pollTransfer();
  } catch (error) {
    $("pi-password").value = "";
    $("backup-login-error").textContent = error.message;
    if (error.message.includes("Pi-Kontos")) csrfToken = "";
  }
});

$("backup-download").addEventListener("click", (event) => {
  if (!activeJobId) { event.preventDefault(); return; }
  $("backup-progress-title").textContent = "Image wird übertragen";
  $("backup-download").hidden = true;
});

async function pollTransfer() {
  if (!activeJobId) return;
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

document.querySelectorAll(".nav-link[href^='#']").forEach((link) => {
  link.addEventListener("click", () => {
    document.querySelectorAll(".nav-link").forEach((item) => {
      item.classList.remove("active");
      item.removeAttribute("aria-current");
    });
    link.classList.add("active");
    link.setAttribute("aria-current", "page");
  });
});

loadDashboard();

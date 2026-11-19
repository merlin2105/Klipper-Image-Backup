let csrfToken = "";
const $ = (id) => document.getElementById(id);

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
    const [system, storage, printer, recovery] = await Promise.all([
      api("/api/system"), api("/api/storage"), api("/api/klipper/status"), api("/api/recovery/status"),
    ]);
    $("hostname").textContent = system.hostname;
    $("host-name").textContent = system.hostname.toUpperCase();
    $("model").textContent = system.model;
    $("os-name").textContent = system.os;
    $("kernel").textContent = system.kernel;
    $("architecture").textContent = system.architecture;
    $("overall-title").textContent = "Diagnose aktiv · Nur lesend";
    $("overall-description").textContent = recovery.installed ? recovery.message : "Recovery-System noch nicht installiert. Es wurden keine Änderungen vorgenommen.";
    $("overall-led").className = "status-led warning";
    renderPrinter(printer);
    renderStorage(storage);
    const blockers = ["Backup-Erstellung ist in dieser Version nicht verfügbar."];
    if (!printer.safe_to_restart) blockers.push(printer.message);
    if (!storage.available || !storage.system_device) blockers.push("Systemlaufwerk konnte nicht eindeutig erkannt werden.");
    $("preflight-message").textContent = blockers.join(" ");
    $("footer-time").textContent = `AKTUALISIERT ${new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`;
  } catch (error) {
    showError(error.message);
  }
}

function renderPrinter(printer) {
  const isReady = printer.classification === "ready";
  const isActive = printer.classification === "active";
  $("printer-icon").className = `printer-icon ${isReady ? "ready" : isActive ? "active" : "unknown"}`;
  $("printer-icon-path").setAttribute("d", isReady ? "m5 12 4 4L19 6" : isActive ? "M8 5v14m8-14v14" : "M12 7v6m0 4h.01");
  $("printer-title").textContent = isReady ? "Bereit" : isActive ? "Druckauftrag aktiv" : "Status unbekannt";
  $("printer-description").textContent = printer.message;
  $("printer-state").textContent = (printer.state || "UNBEKANNT").toUpperCase();
  $("restart-safety").textContent = `NEUSTART: ${printer.safe_to_restart ? "PRÜFUNG BESTANDEN" : "GESPERRT"}`;
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
  $("image-estimate").textContent = storage.estimated_minimum_image_bytes ? formatBytes(storage.estimated_minimum_image_bytes) : "Noch nicht berechenbar · Shrinking nicht verfügbar";
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

async function boot() {
  try {
    const session = await api("/api/session");
    if (!session.authenticated) {
      $("login-panel").hidden = false;
      return;
    }
    csrfToken = session.csrf_token;
    $("dashboard-content").hidden = false;
    $("logout").hidden = false;
    await loadDashboard();
  } catch (error) {
    $("login-panel").hidden = false;
    $("login-error").textContent = `Dienst nicht erreichbar: ${error.message}`;
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    const result = await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("password").value }) });
    csrfToken = result.csrf_token;
    $("login-panel").hidden = true;
    $("dashboard-content").hidden = false;
    $("logout").hidden = false;
    await loadDashboard();
  } catch (error) { $("login-error").textContent = error.message; }
});

$("logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); window.location.reload(); }
  catch (error) { showError(error.message); }
});

$("preflight").addEventListener("click", async () => {
  $("preflight-message").textContent = "Prüfung läuft …";
  try {
    const result = await api("/api/backup/preflight", { method: "POST", body: "{}" });
    $("preflight-message").textContent = result.blockers.join(" ");
    renderPrinter(result.printer);
    renderStorage(result.storage);
  } catch (error) { $("preflight-message").textContent = error.message; }
});

boot();

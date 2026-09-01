let config = null;
let selectedBollardName = null;
let drawing = false;
let dragStart = null;

const snapshotImg = document.getElementById("snapshot");
const overlay = document.getElementById("overlay");
const ctx = overlay.getContext("2d");

function defaultBollard(name) {
  return {
    name,
    display_name: name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
    roi: [100, 100, 60, 200],
    led_raised_y_range: [0, 40],
    led_lowered_y_range: [160, 200],
    led_target_type: "white",
    led_hue_range: [0, 15],
    led_min_saturation: 120,
    led_max_saturation_white: 60,
    led_min_value_day: 180,
    led_min_value_night: 220,
  };
}

function updateTargetTypeFieldVisibility() {
  const type = document.getElementById("f-target-type").value;
  document.getElementById("color-fields").style.display = type === "color" ? "block" : "none";
  document.getElementById("white-fields").style.display = type === "white" ? "block" : "none";
}
document.getElementById("f-target-type").addEventListener("change", updateTargetTypeFieldVisibility);

async function loadConfig() {
  const res = await fetch("/api/config");
  config = await res.json();
  if (!config.bollards) config.bollards = [];
  renderTabs();
  fillConnectionForm();
  if (config.bollards.length && !selectedBollardName) {
    selectBollard(config.bollards[0].name);
  }
}

function fillConnectionForm() {
  document.getElementById("f-rtsp-url").value = config.rtsp_url || "";
  const m = config.mqtt || {};
  document.getElementById("f-mqtt-enabled").checked = !!m.enabled;
  document.getElementById("f-mqtt-host").value = m.host || "";
  document.getElementById("f-mqtt-port").value = m.port || 1883;
  document.getElementById("f-mqtt-user").value = m.username || "";
  document.getElementById("f-mqtt-pass").value = m.password || "";
  document.getElementById("f-mqtt-topic").value = m.base_topic || "bollards";
}

function renderTabs() {
  const tabs = document.getElementById("bollard-tabs");
  tabs.innerHTML = "";
  config.bollards.forEach(b => {
    const btn = document.createElement("button");
    btn.textContent = b.display_name || b.name;
    btn.className = "tab-btn" + (b.name === selectedBollardName ? " active" : "");
    btn.onclick = () => selectBollard(b.name);
    tabs.appendChild(btn);
  });
}

function selectBollard(name) {
  selectedBollardName = name;
  const b = config.bollards.find(x => x.name === name);
  if (!b) return;
  document.getElementById("bollard-editor").style.display = "block";
  document.getElementById("f-name").value = b.name;
  document.getElementById("f-display-name").value = b.display_name;
  document.getElementById("f-roi-x").value = b.roi[0];
  document.getElementById("f-roi-y").value = b.roi[1];
  document.getElementById("f-roi-w").value = b.roi[2];
  document.getElementById("f-roi-h").value = b.roi[3];
  document.getElementById("f-target-type").value = b.led_target_type || "white";
  document.getElementById("f-hue0").value = b.led_hue_range[0];
  document.getElementById("f-hue1").value = b.led_hue_range[1];
  document.getElementById("f-sat").value = b.led_min_saturation;
  document.getElementById("f-max-sat-white").value = b.led_max_saturation_white != null ? b.led_max_saturation_white : 60;
  document.getElementById("f-val-day").value = b.led_min_value_day;
  document.getElementById("f-val-night").value = b.led_min_value_night;
  updateTargetTypeFieldVisibility();
  document.getElementById("raised-range-display").textContent =
    `current: [${b.led_raised_y_range[0]}, ${b.led_raised_y_range[1]}]`;
  document.getElementById("lowered-range-display").textContent =
    `current: [${b.led_lowered_y_range[0]}, ${b.led_lowered_y_range[1]}]`;
  renderTabs();
  drawOverlay();
}

function readEditorIntoBollardObject() {
  const b = config.bollards.find(x => x.name === selectedBollardName) || defaultBollard("new_bollard");
  b.name = document.getElementById("f-name").value.trim();
  b.display_name = document.getElementById("f-display-name").value.trim();
  b.roi = [
    parseInt(document.getElementById("f-roi-x").value) || 0,
    parseInt(document.getElementById("f-roi-y").value) || 0,
    parseInt(document.getElementById("f-roi-w").value) || 10,
    parseInt(document.getElementById("f-roi-h").value) || 10,
  ];
  b.led_target_type = document.getElementById("f-target-type").value;
  b.led_hue_range = [
    parseInt(document.getElementById("f-hue0").value) || 0,
    parseInt(document.getElementById("f-hue1").value) || 15,
  ];
  b.led_min_saturation = parseInt(document.getElementById("f-sat").value) || 120;
  b.led_max_saturation_white = parseInt(document.getElementById("f-max-sat-white").value) || 60;
  b.led_min_value_day = parseInt(document.getElementById("f-val-day").value) || 180;
  b.led_min_value_night = parseInt(document.getElementById("f-val-night").value) || 220;
  return b;
}

async function saveConfig() {
  const res = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  const status = document.getElementById("save-status");
  if (res.ok) {
    status.textContent = "Saved.";
    status.style.color = "#4caf50";
  } else {
    const err = await res.json();
    status.textContent = "Error: " + err.error;
    status.style.color = "#f44336";
  }
  setTimeout(() => (status.textContent = ""), 3000);
}

document.getElementById("save-bollard-btn").onclick = () => {
  const updated = readEditorIntoBollardObject();
  const idx = config.bollards.findIndex(x => x.name === selectedBollardName);
  if (idx >= 0) {
    config.bollards[idx] = updated;
  } else {
    config.bollards.push(updated);
  }
  selectedBollardName = updated.name;
  saveConfig().then(() => { renderTabs(); drawOverlay(); });
};

document.getElementById("delete-bollard-btn").onclick = () => {
  if (!confirm("Delete this bollard?")) return;
  config.bollards = config.bollards.filter(x => x.name !== selectedBollardName);
  selectedBollardName = config.bollards.length ? config.bollards[0].name : null;
  saveConfig().then(() => {
    renderTabs();
    document.getElementById("bollard-editor").style.display = selectedBollardName ? "block" : "none";
    if (selectedBollardName) selectBollard(selectedBollardName);
    drawOverlay();
  });
};

document.getElementById("add-bollard-btn").onclick = () => {
  let n = 1;
  while (config.bollards.some(b => b.name === `bollard_${n}`)) n++;
  const nb = defaultBollard(`bollard_${n}`);
  config.bollards.push(nb);
  selectBollard(nb.name);
  saveConfig();
};

document.getElementById("save-connection-btn").onclick = () => {
  config.rtsp_url = document.getElementById("f-rtsp-url").value.trim();
  config.mqtt = {
    enabled: document.getElementById("f-mqtt-enabled").checked,
    host: document.getElementById("f-mqtt-host").value.trim(),
    port: parseInt(document.getElementById("f-mqtt-port").value) || 1883,
    username: document.getElementById("f-mqtt-user").value.trim(),
    password: document.getElementById("f-mqtt-pass").value,
    base_topic: document.getElementById("f-mqtt-topic").value.trim() || "bollards",
  };
  saveConfig();
};

async function calibrate(which) {
  if (!selectedBollardName) return;
  // Save current ROI edits first so calibration reads against the right box.
  const updated = readEditorIntoBollardObject();
  const idx = config.bollards.findIndex(x => x.name === selectedBollardName);
  if (idx >= 0) config.bollards[idx] = updated;
  await saveConfig();

  const res = await fetch("/api/calibrate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: selectedBollardName, which }),
  });
  const body = await res.json();
  if (!res.ok) {
    alert("Calibration failed: " + body.error);
    return;
  }
  await loadConfig();
  selectBollard(selectedBollardName);
}

document.getElementById("calib-raised-btn").onclick = () => calibrate("raised");
document.getElementById("calib-lowered-btn").onclick = () => calibrate("lowered");

// --- Canvas ROI drawing ---

function resizeOverlay() {
  overlay.width = snapshotImg.clientWidth;
  overlay.height = snapshotImg.clientHeight;
}

function scaleFactors() {
  return {
    sx: snapshotImg.naturalWidth ? overlay.width / snapshotImg.naturalWidth : 1,
    sy: snapshotImg.naturalHeight ? overlay.height / snapshotImg.naturalHeight : 1,
  };
}

function drawOverlay() {
  if (!config) return;
  resizeOverlay();
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  const { sx, sy } = scaleFactors();
  config.bollards.forEach(b => {
    const [x, y, w, h] = b.roi;
    ctx.strokeStyle = b.name === selectedBollardName ? "#00e5ff" : "#888";
    ctx.lineWidth = b.name === selectedBollardName ? 2 : 1;
    ctx.strokeRect(x * sx, y * sy, w * sx, h * sy);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.font = "12px sans-serif";
    ctx.fillText(b.display_name || b.name, x * sx, Math.max(y * sy - 4, 10));
  });
}

overlay.addEventListener("mousedown", e => {
  if (!selectedBollardName) return;
  drawing = true;
  const rect = overlay.getBoundingClientRect();
  dragStart = { x: e.clientX - rect.left, y: e.clientY - rect.top };
});

overlay.addEventListener("mousemove", e => {
  if (!drawing) return;
  const rect = overlay.getBoundingClientRect();
  const cur = { x: e.clientX - rect.left, y: e.clientY - rect.top };
  drawOverlay();
  ctx.strokeStyle = "#00e5ff";
  ctx.lineWidth = 2;
  ctx.strokeRect(dragStart.x, dragStart.y, cur.x - dragStart.x, cur.y - dragStart.y);
});

overlay.addEventListener("mouseup", e => {
  if (!drawing) return;
  drawing = false;
  const rect = overlay.getBoundingClientRect();
  const cur = { x: e.clientX - rect.left, y: e.clientY - rect.top };
  const { sx, sy } = scaleFactors();

  const x0 = Math.min(dragStart.x, cur.x) / sx;
  const y0 = Math.min(dragStart.y, cur.y) / sy;
  const w = Math.abs(cur.x - dragStart.x) / sx;
  const h = Math.abs(cur.y - dragStart.y) / sy;

  if (w < 5 || h < 5) return; // ignore accidental clicks

  document.getElementById("f-roi-x").value = Math.round(x0);
  document.getElementById("f-roi-y").value = Math.round(y0);
  document.getElementById("f-roi-w").value = Math.round(w);
  document.getElementById("f-roi-h").value = Math.round(h);

  const b = config.bollards.find(x => x.name === selectedBollardName);
  if (b) {
    b.roi = [Math.round(x0), Math.round(y0), Math.round(w), Math.round(h)];
    drawOverlay();
  }
});

// --- Snapshot + state polling ---

function refreshSnapshot() {
  snapshotImg.src = "/api/snapshot.jpg?ts=" + Date.now();
}

snapshotImg.addEventListener("load", drawOverlay);
window.addEventListener("resize", drawOverlay);

document.getElementById("refresh-btn").onclick = refreshSnapshot;

async function pollState() {
  try {
    const res = await fetch("/api/state");
    const body = await res.json();
    const statusEl = document.getElementById("stream-status");
    if (body.stream_ok) {
      statusEl.textContent = "stream connected";
      statusEl.className = "badge badge-ok";
    } else {
      statusEl.textContent = "stream error: " + (body.stream_error || "unknown");
      statusEl.className = "badge badge-error";
    }

    const list = document.getElementById("states-list");
    list.innerHTML = "";
    Object.entries(body.states).forEach(([name, state]) => {
      const row = document.createElement("div");
      row.className = "state-row";
      const label = document.createElement("span");
      label.textContent = name;
      const badge = document.createElement("span");
      badge.textContent = state;
      badge.className = "badge state-" + state.toLowerCase();
      row.appendChild(label);
      row.appendChild(badge);
      list.appendChild(row);
    });
  } catch (e) {
    // ignore transient errors
  }
}

document.getElementById("auto-refresh").addEventListener("change", e => {
  autoRefreshEnabled = e.target.checked;
});
let autoRefreshEnabled = true;

setInterval(() => {
  if (autoRefreshEnabled) refreshSnapshot();
  pollState();
}, 2000);

loadConfig();
pollState();

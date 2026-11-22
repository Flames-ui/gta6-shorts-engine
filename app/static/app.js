/* GTA 6 Shorts Engine Frontend SPA Controller */
document.addEventListener("DOMContentLoaded", () => {
  let API_KEY = sessionStorage.getItem("gta6_api_key");
  if (!API_KEY) {
    API_KEY = window.prompt("Enter your GTA 6 Studio API key");
    if (API_KEY) sessionStorage.setItem("gta6_api_key", API_KEY);
  }
  const headers = {
    "Content-Type": "application/json",
    ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
  };
  const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));

  // State
  let currentClipId = null;
  let activeTimeline = null;

  async function loadProtectedVideo(videoElement, url) {
    const response = await fetch(url, { headers: { "X-API-Key": API_KEY || "" } });
    if (!response.ok) throw new Error(`Video preview unavailable (${response.status})`);
    const previous = videoElement.dataset.objectUrl;
    if (previous) URL.revokeObjectURL(previous);
    const objectUrl = URL.createObjectURL(await response.blob());
    videoElement.dataset.objectUrl = objectUrl;
    videoElement.src = objectUrl;
  }

  // Tab Navigation
  const navLinks = document.querySelectorAll(".nav-link");
  const tabSections = document.querySelectorAll(".tab-section");
  const pageTitle = document.getElementById("page-title");

  navLinks.forEach((link) => {
    link.addEventListener("click", () => {
      const tabId = link.getAttribute("data-tab");
      navLinks.forEach((l) => l.classList.remove("active"));
      tabSections.forEach((s) => s.classList.remove("active"));

      link.classList.add("active");
      const targetSection = document.getElementById(`tab-${tabId}`);
      if (targetSection) targetSection.classList.add("active");

      pageTitle.innerHTML = link.innerHTML;
      loadTabData(tabId);
    });
  });

  function loadTabData(tabId) {
    if (tabId === "studio") loadStudioData();
    if (tabId === "autonomous") loadAutonomousData();
    if (tabId === "registry") loadRegistryData();
    if (tabId === "outro") loadOutroData();
    if (tabId === "library") loadLibraryData();
    if (tabId === "publishing") loadPublishingData();
    if (tabId === "health") loadHealthData();
  }

  // Real-time SSE Connection
  async function setupSSE() {
    if (!API_KEY) return;
    const badge = document.getElementById("sse-badge");
    try {
      const response = await fetch("/api/v1/studio/stream/global", { headers: { "X-API-Key": API_KEY } });
      if (!response.ok || !response.body) throw new Error(`Stream unavailable (${response.status})`);
      badge.className = "badge badge-info";
      badge.textContent = "STREAM OPEN";
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() || "";
        for (const frame of frames) {
          const event = frame.match(/^event: (.+)$/m)?.[1];
          const dataLine = frame.match(/^data: (.+)$/m)?.[1];
          if (!event || !dataLine) continue;
          const payload = JSON.parse(dataLine);
          if (event === "clip_produced") {
            appendConsoleLog(`[Autonomous] Rendered short: ${payload.data.title} (Score: ${payload.data.score})`, "success");
            loadAutonomousData(); loadLibraryData();
          } else if (event === "autonomous_cycle_started") {
            appendConsoleLog(`[Autonomous] Cycle started: minimum score ${payload.data.min_score}`, "warning");
          } else {
            appendConsoleLog(`[Studio] ${event.replaceAll("_", " ")}`, "normal");
          }
        }
      }
    } catch (error) {
      badge.className = "badge badge-warning";
      badge.textContent = "STREAM UNAVAILABLE";
      appendConsoleLog("Authenticated live activity stream is unavailable.", "warning");
    }
  }

  function appendConsoleLog(msg, type = "normal") {
    const logBox = document.getElementById("studio-console-log");
    if (!logBox) return;
    const line = document.createElement("div");
    line.className = `console-line ${type}`;
    const time = new Date().toLocaleTimeString();
    line.textContent = `[${time}] ${msg}`;
    logBox.appendChild(line);
    logBox.scrollTop = logBox.scrollHeight;
  }

  // 1. Clip Studio Data
  async function loadStudioData() {
    if (!currentClipId) return;
    try {
      const res = await fetch(`/api/v1/studio/clips/${currentClipId}/timeline`, { headers });
      if (res.ok) {
        activeTimeline = await res.json();
        const hookInput = document.getElementById("inspector-hook-text");
        if (hookInput && activeTimeline.hooks_track) {
          hookInput.value = activeTimeline.hooks_track.text || "";
        }
        const hookDisplay = document.getElementById("studio-hook-display");
        if (hookDisplay && activeTimeline.hooks_track) {
          hookDisplay.textContent = activeTimeline.hooks_track.text || "";
        }
        const outroToggle = document.getElementById("inspector-outro-toggle");
        if (outroToggle && activeTimeline.outro_track) outroToggle.checked = !!activeTimeline.outro_track.enabled;
        const durationLabel = document.getElementById("studio-video-duration");
        if (durationLabel) durationLabel.textContent = `${activeTimeline.duration}s · Source: ${activeTimeline.source_url ? "available" : "not linked"}`;
        const timecode = document.getElementById("studio-timecode");
        if (timecode) timecode.textContent = `00:00 / ${activeTimeline.duration}s`;
        const player = document.getElementById("studio-video-player");
        if (player && activeTimeline.source_url) {
          try { await loadProtectedVideo(player, `/api/v1/studio/clips/${encodeURIComponent(currentClipId)}/video`); } catch (_) {}
        }
      }
    } catch (e) {
      console.warn("Studio timeline fetch notice:", e);
    }
  }

  // Save Timeline
  const btnSaveTimeline = document.getElementById("btn-save-timeline");
  if (btnSaveTimeline) {
    btnSaveTimeline.addEventListener("click", async () => {
      if (!activeTimeline) return;
      activeTimeline.hooks_track.text = document.getElementById("inspector-hook-text").value;
      activeTimeline.outro_track.enabled = document.getElementById("inspector-outro-toggle").checked;

      const hookDisplay = document.getElementById("studio-hook-display");
      if (hookDisplay) hookDisplay.textContent = activeTimeline.hooks_track.text;

      try {
        const res = await fetch(`/api/v1/studio/clips/${currentClipId}/timeline`, {
          method: "POST",
          headers,
          body: JSON.stringify(activeTimeline),
        });
        if (res.ok) {
          appendConsoleLog("[Studio] Multitrack timeline saved successfully.", "success");
        }
      } catch (err) {
        appendConsoleLog(`[Studio] Error saving timeline: ${err}`, "warning");
      }
    });
  }

  // Render Studio Short
  const btnRenderStudio = document.getElementById("btn-render-studio");
  if (btnRenderStudio) {
    btnRenderStudio.addEventListener("click", async () => {
      btnRenderStudio.disabled = true;
      btnRenderStudio.textContent = "Queueing render...";
      appendConsoleLog("[Studio] Executing FFmpeg 6-track synthesis & outro injection...", "warning");

      try {
        const res = await fetch(`/api/v1/studio/clips/${currentClipId}/render`, {
          method: "POST",
          headers,
        });
        const data = await res.json();
        if (res.ok) appendConsoleLog(`[Studio] Render queued as ${data.job_id}.`, "success");
        else appendConsoleLog(`[Studio] ${data.detail || "Render could not be queued."}`, "warning");
      } catch (err) {
        appendConsoleLog(`[Studio] Render error: ${err}`, "warning");
      } finally {
        btnRenderStudio.disabled = false;
        btnRenderStudio.textContent = "⚡ Render Vertical Clip"
      }
    });
  }

  // 2. Autonomous Data
  async function loadAutonomousData() {
    try {
      const res = await fetch("/api/v1/autonomous/stats", { headers });
      if (res.ok) {
        const stats = await res.json();
        document.getElementById("auto-stat-today").textContent = `${stats.produced_today} / ${stats.daily_target}`;
        document.getElementById("auto-stat-published").textContent = stats.published_today;
        document.getElementById("auto-stat-score").textContent = stats.average_viral_score ?? "—";
        document.getElementById("auto-stat-status").textContent = stats.is_active ? "ACTIVE" : "PAUSED";
        document.getElementById("auto-config-active").checked = stats.is_active;
        document.getElementById("auto-config-publish").checked = stats.auto_publish;
        document.getElementById("auto-config-daily").value = stats.daily_target;
        document.getElementById("auto-config-minscore").value = stats.min_score_threshold;
      }
    } catch (e) {
      console.warn("Autonomous stats error:", e);
    }
  }

  // 3. Registry & Feed Data
  async function loadRegistryData() {
    try {
      // Sources
      const sRes = await fetch("/api/v1/discovery/sources", { headers });
      if (sRes.ok) {
        const sData = await sRes.json();
        const tbody = document.getElementById("sources-table-body");
        tbody.innerHTML = "";
        (sData.sources || []).forEach((src) => {
          const row = document.createElement("tr");
          const canDisable = Boolean(src.is_active);
          row.innerHTML = `
            <td><strong>${escapeHTML(src.name)}</strong></td>
            <td><span class="badge badge-info">${escapeHTML(src.source_type)}</span></td>
            <td><span class="badge ${String(src.rights_status).startsWith("authorized") ? "badge-success" : "badge-warning"}">${escapeHTML(src.rights_status || "pending_verification")}</span></td>
            <td style="color: #94a3b8; font-size: 12px;">${escapeHTML((src.allowed_platforms || []).join(", ") || "No approved platforms")}</td>
            <td>${canDisable ? `<button class="btn btn-secondary btn-sm" data-source-id="${escapeHTML(src.id)}">Disable</button>` : "Monitoring off"}</td>
          `;
          const disableButton = row.querySelector("button[data-source-id]");
          if (disableButton) disableButton.addEventListener("click", async () => {
            const result = await fetch(`/api/v1/discovery/sources/${encodeURIComponent(src.id)}`, { method: "DELETE", headers });
            if (result.ok) loadRegistryData();
          });
          tbody.appendChild(row);
        });
      }

      // Moments
      const mRes = await fetch("/api/v1/discovery/moments", { headers });
      if (mRes.ok) {
        const mData = await mRes.json();
        const tbody = document.getElementById("moments-table-body");
        tbody.innerHTML = "";
        (mData.moments || []).forEach((m) => {
          const row = document.createElement("tr");
          row.innerHTML = `
            <td><div><strong>${escapeHTML(m.title)}</strong></div><div style="font-size: 12px; color: #f43f5e;">${escapeHTML(m.hook_text || "")}</div></td>
            <td><span class="badge badge-warning" style="font-size: 12px;">${escapeHTML(m.score)} / 10</span></td>
            <td>${escapeHTML(m.duration)}s</td>
            <td><span class="badge badge-info">${escapeHTML(m.event_type)}</span></td>
            <td><span class="badge ${m.status === "approved" ? "badge-success" : "badge-warning"}">${escapeHTML(m.status)}</span></td>
            <td><button class="btn btn-primary btn-sm" data-moment-id="${escapeHTML(m.id)}">Approve</button></td>
          `;
          row.querySelector("button").addEventListener("click", () => window.approveMoment(m.id));
          tbody.appendChild(row);
        });
      }
    } catch (e) {
      console.warn("Registry error:", e);
    }
  }

  window.approveMoment = async (momentId) => {
    try {
      await fetch(`/api/v1/discovery/moments/${momentId}/approve`, { method: "POST", headers });
      appendConsoleLog(`[Registry] Candidate ${momentId} approved; no clip was produced.`, "success");
      loadRegistryData();
    } catch (e) {
      console.error(e);
    }
  };

  // 4. Outro Data
  async function loadOutroData() {
    try {
      const res = await fetch("/api/v1/outro/config", { headers });
      if (res.ok) {
        const cfg = await res.json();
        document.getElementById("outro-promo-text").value = cfg.promo_text || "";
        document.getElementById("outro-channel-url").value = cfg.destination_url || "";
        document.getElementById("outro-duration").value = cfg.duration_seconds || 4.0;
        document.getElementById("outro-enabled").checked = !!cfg.enabled;
        const preview = document.getElementById("outro-video-preview");
        if (cfg.enabled && preview) loadProtectedVideo(preview, "/api/v1/outro/preview").catch(() => {});
      }
    } catch (e) {
      console.warn("Outro config error:", e);
    }
  }

  // Regenerate Outro
  const btnRegenOutro = document.getElementById("btn-regenerate-outro");
  if (btnRegenOutro) {
    btnRegenOutro.addEventListener("click", async () => {
      btnRegenOutro.disabled = true;
      btnRegenOutro.textContent = "Synthesizing Outro...";
      const promoText = document.getElementById("outro-promo-text").value;
      const channelUrl = document.getElementById("outro-channel-url").value;
      const dur = parseFloat(document.getElementById("outro-duration").value);

      try {
        const res = await fetch("/api/v1/outro/generate", {
          method: "POST",
          headers,
          body: JSON.stringify({ promo_text: promoText, destination_url: channelUrl, duration_seconds: dur }),
        });
        if (res.ok) {
          appendConsoleLog("[Outro Studio] Generated a silent animated promo video with a tone track.", "success");
          const v = document.getElementById("outro-video-preview");
          if (v) {
            await loadProtectedVideo(v, `/api/v1/outro/preview?t=${Date.now()}`);
            v.play();
          }
        }
      } finally {
        btnRegenOutro.disabled = false;
        btnRegenOutro.textContent = "Regenerate Outro Video";
      }
    });
  }

  // 5. Library Data
  async function loadLibraryData() {
    try {
      const res = await fetch("/api/v1/clips", { headers });
      if (res.ok) {
        const data = await res.json();
        const tbody = document.getElementById("clips-table-body");
        tbody.innerHTML = "";
        (data.clips || []).forEach((c) => {
          const row = document.createElement("tr");
          row.innerHTML = `
            <td><strong>${escapeHTML(c.title)}</strong></td>
            <td>${escapeHTML(c.duration ?? "—")}s</td>
            <td><span class="badge badge-info">${escapeHTML(c.publishing_status || "Not published")}</span></td>
            <td>${escapeHTML((c.created_at || "").substring(0, 10))}</td>
            <td><button class="btn btn-secondary btn-sm" data-clip-id="${escapeHTML(c.id)}">Open in Studio</button></td>
          `;
          row.querySelector("button").addEventListener("click", () => window.openClipInStudio(c.id));
          tbody.appendChild(row);
        });
      }
    } catch (e) {
      console.warn("Clips library error:", e);
    }
  }

  window.openClipInStudio = (clipId) => {
    currentClipId = clipId;
    document.querySelector('.nav-link[data-tab="studio"]').click();
  };

  // Save Autonomous Config
  const btnSaveAuto = document.getElementById("btn-save-autonomous-config");
  if (btnSaveAuto) {
    btnSaveAuto.addEventListener("click", async () => {
      const payload = {
        is_active: document.getElementById("auto-config-active").checked,
        auto_publish: document.getElementById("auto-config-publish").checked,
        daily_target: parseInt(document.getElementById("auto-config-daily").value, 10),
        min_score: parseFloat(document.getElementById("auto-config-minscore").value),
        outro_enabled: true,
      };
      const res = await fetch("/api/v1/autonomous/config", { method: "POST", headers, body: JSON.stringify(payload) });
      if (res.ok) {
        appendConsoleLog("[Autonomous] Target settings saved; no automated production is running.", "warning");
        loadAutonomousData();
      }
    });
  }

  // Save Outro Configuration
  const btnSaveOutro = document.getElementById("btn-save-outro");
  if (btnSaveOutro) {
    btnSaveOutro.addEventListener("click", async () => {
      const payload = {
        enabled: document.getElementById("outro-enabled").checked,
        promo_text: document.getElementById("outro-promo-text").value,
        destination_url: document.getElementById("outro-channel-url").value,
        duration_seconds: parseFloat(document.getElementById("outro-duration").value),
        qr_code_enabled: false,
      };
      const res = await fetch("/api/v1/outro/config", { method: "POST", headers, body: JSON.stringify(payload) });
      if (res.ok) appendConsoleLog("[Outro Studio] Outro configuration saved.", "success");
      else { const data = await res.json(); appendConsoleLog(`[Outro Studio] ${data.detail || "Settings could not be saved."}`, "warning"); }
    });
  }

  // 6. Publishing Data
  async function loadPublishingData() {
    try {
      // 1. Fetch connected accounts
      const accRes = await fetch("/api/v1/publishing/accounts", { headers });
      let accounts = [];
      if (accRes.ok) {
        const accData = await accRes.json();
        accounts = accData.accounts || [];
      }

      // 2. Fetch connected channels (Buffer profiles)
      const chRes = await fetch("/api/v1/publishing/channels", { headers });
      let channels = [];
      if (chRes.ok) {
        const chData = await chRes.json();
        channels = chData.channels || [];
      }

      // Update Buffer Status
      const bufferAcc = accounts.find(a => a.service === "buffer");
      const bufferBadge = document.getElementById("buffer-conn-badge");
      const bufferInfo = document.getElementById("buffer-channel-info");
      const tiktokCh = channels.find(c => c.service === "tiktok");

      if (bufferBadge) {
        if (bufferAcc || tiktokCh) {
          bufferBadge.className = "badge badge-success";
          bufferBadge.textContent = "CONNECTED";
        } else {
          bufferBadge.className = "badge badge-warning";
          bufferBadge.textContent = "NOT CONNECTED";
        }
      }
      if (bufferInfo) {
        bufferInfo.textContent = tiktokCh ? `Connected Channel: @${tiktokCh.formatted_username}` : "No TikTok channel linked in Buffer yet.";
      }

      // Update YouTube Status
      const ytAcc = accounts.find(a => a.service === "youtube");
      const ytBadge = document.getElementById("youtube-conn-badge");
      const ytInfo = document.getElementById("youtube-channel-info");

      if (ytBadge) {
        if (ytAcc && ytAcc.has_token) {
          ytBadge.className = "badge badge-success";
          ytBadge.textContent = "CONNECTED";
        } else {
          ytBadge.className = "badge badge-warning";
          ytBadge.textContent = "NOT CONNECTED";
        }
      }
      if (ytInfo) {
        ytInfo.textContent = ytAcc ? `Connected Account: ${ytAcc.id}` : "No Google YouTube channel authorized yet.";
      }

      // 3. Fetch internal publishing queue
      const qRes = await fetch("/api/v1/publishing/queue", { headers });
      const tbody = document.getElementById("publishing-queue-body");
      const qBadge = document.getElementById("queue-count-badge");

      if (qRes.ok && tbody) {
        const qData = await qRes.json();
        const items = qData.items || [];
        if (qBadge) qBadge.textContent = `${items.length} items`;

        tbody.innerHTML = "";
        if (items.length === 0) {
          tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 20px;">No scheduled publications. Approve clips in the library or run the autonomous engine to schedule.</td></tr>`;
        } else {
          items.forEach(it => {
            const row = document.createElement("tr");
            const isPub = it.status === "published";
            const liveUrl = it.published_url || (it.platform === "youtube" && it.platform_update_id ? `https://www.youtube.com/shorts/${it.platform_update_id}` : null);
            const statusBadgeClass = isPub ? "badge-success" : (it.status === "failed" ? "badge-danger" : (it.status === "publishing" ? "badge-warning" : "badge-info"));

            row.innerHTML = `
              <td><strong>${escapeHTML(it.clip_id || it.id)}</strong></td>
              <td><span class="badge ${it.platform === "youtube" ? "badge-danger" : "badge-info"}">${escapeHTML(it.platform ? it.platform.toUpperCase() : "TIKTOK")}</span></td>
              <td style="font-size: 12px; color: var(--text-muted);">${escapeHTML(it.scheduled_for ? it.scheduled_for.replace("T", " ").slice(0, 19) : "Immediate")}</td>
              <td><span class="badge ${statusBadgeClass}">${escapeHTML(it.status || "scheduled")}</span></td>
              <td style="font-size: 12px;">${liveUrl ? `<a href="${escapeHTML(liveUrl)}" target="_blank" style="color: var(--accent-cyan); text-decoration: underline;">View Post</a>` : (it.platform_update_id ? escapeHTML(it.platform_update_id) : "—")}</td>
              <td>
                ${!isPub ? `<button class="btn btn-primary btn-sm" data-publish-now="${escapeHTML(it.id)}" data-clip="${escapeHTML(it.clip_id)}" data-plat="${escapeHTML(it.platform || "tiktok")}">Publish Now</button>` : `<span style="color: var(--accent-emerald); font-size: 12px;">✓ Published</span>`}
              </td>
            `;

            const pubBtn = row.querySelector("button[data-publish-now]");
            if (pubBtn) {
              pubBtn.addEventListener("click", async () => {
                pubBtn.disabled = true;
                pubBtn.textContent = "Publishing...";
                try {
                  const pRes = await fetch("/api/v1/publishing/publish-now", {
                    method: "POST",
                    headers,
                    body: JSON.stringify({ clip_id: it.clip_id, platform: it.platform || "tiktok" }),
                  });
                  const pData = await pRes.json();
                  if (pRes.ok && pData.success) {
                    alert(`Published successfully to ${it.platform}!`);
                  } else {
                    alert(`Publishing response: ${pData.error || pData.status || "Completed"}`);
                  }
                  loadPublishingData();
                } catch (err) {
                  alert(`Error: ${err.message}`);
                  pubBtn.disabled = false;
                  pubBtn.textContent = "Publish Now";
                }
              });
            }

            tbody.appendChild(row);
          });
        }
      }

      // 4. Load latest reconciliation report
      loadReconciliationReport();

    } catch (e) {
      console.warn("Publishing load error:", e);
    }
  }

  async function loadReconciliationReport() {
    try {
      const repRes = await fetch("/api/v1/publishing/reconciliation-reports?limit=1", { headers });
      if (repRes.ok) {
        const repData = await repRes.json();
        const reports = repData.reports || [];
        const container = document.getElementById("reconciliation-report-container");
        const healthBadge = document.getElementById("reconcile-health-badge");

        if (container && reports.length > 0) {
          const r = reports[0];
          if (healthBadge) {
            healthBadge.className = `badge ${r.system_health === "healthy" ? "badge-success" : "badge-warning"}`;
            healthBadge.textContent = (r.system_health || "HEALTHY").toUpperCase();
          }

          container.innerHTML = `
            <div style="background: var(--bg-surface); padding: 16px; border-radius: 8px; border: 1px solid var(--border-subtle); margin-top: 8px;">
              <div style="display: flex; justify-content: space-between; margin-bottom: 12px;">
                <span><strong>Cycle ID:</strong> ${escapeHTML(r.id)}</span>
                <span style="color: var(--text-dim);">${escapeHTML(r.generated_at ? r.generated_at.replace("T", " ").slice(0, 19) : "")} UTC</span>
              </div>
              <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 12px;">
                <div style="background: var(--bg-deep); padding: 10px; border-radius: 6px;">
                  <div style="font-size: 11px; color: var(--text-dim);">TOTAL CHECKED</div>
                  <div style="font-size: 18px; font-weight: bold;">${r.total_scheduled}</div>
                </div>
                <div style="background: var(--bg-deep); padding: 10px; border-radius: 6px;">
                  <div style="font-size: 11px; color: var(--text-dim);">CONFIRMED PUBLISHED</div>
                  <div style="font-size: 18px; font-weight: bold; color: var(--accent-emerald);">${r.total_published}</div>
                </div>
                <div style="background: var(--bg-deep); padding: 10px; border-radius: 6px;">
                  <div style="font-size: 11px; color: var(--text-dim);">FAILED</div>
                  <div style="font-size: 18px; font-weight: bold; color: var(--accent-pink);">${r.total_failed}</div>
                </div>
                <div style="background: var(--bg-deep); padding: 10px; border-radius: 6px;">
                  <div style="font-size: 11px; color: var(--text-dim);">TIKTOK / YOUTUBE</div>
                  <div style="font-size: 18px; font-weight: bold;">${r.tiktok_summary ? r.tiktok_summary.confirmed_published : 0} / ${r.youtube_summary ? r.youtube_summary.confirmed_published : 0}</div>
                </div>
              </div>
              ${r.alerts && r.alerts.length > 0 ? `
                <div style="margin-top: 10px; padding: 8px 12px; background: rgba(244, 63, 94, 0.15); border-left: 3px solid var(--accent-pink); border-radius: 4px; font-size: 12px;">
                  ${r.alerts.map(a => `<div>⚠️ ${escapeHTML(a)}</div>`).join("")}
                </div>
              ` : `<div style="color: var(--accent-emerald); font-size: 12px;">✓ All scheduled items reconciled with remote providers. No sync discrepancies.</div>`}
            </div>
          `;
        }
      }
    } catch (e) {
      console.warn("Reconciliation report load error:", e);
    }
  }

  // 7. Health Data
  async function loadHealthData() {
    try {
      const res = await fetch("/health");
      if (res.ok) {
        const h = await res.json();
        document.getElementById("health-env").textContent = h.environment || "Not reported";
        document.getElementById("health-db").textContent = h.database || h.database_status || "Not reported";
      }
    } catch (e) {
      console.warn("Health error:", e);
    }
  }

  // Refresh Button
  document.getElementById("btn-refresh-data").addEventListener("click", () => {
    const activeLink = document.querySelector(".nav-link.active");
    if (activeLink) loadTabData(activeLink.getAttribute("data-tab"));
  });

  // Initial setup
  fetch("/health").then((response) => {
    const badge = document.getElementById("api-status-badge");
    if (response.ok) { badge.className = "badge badge-success"; badge.textContent = "AVAILABLE"; }
    else { badge.className = "badge badge-warning"; badge.textContent = "UNAVAILABLE"; }
  }).catch(() => {
    const badge = document.getElementById("api-status-badge");
    badge.className = "badge badge-warning"; badge.textContent = "UNAVAILABLE";
  });
  setupSSE();
  loadStudioData();
});
    // Connect Buffer OAuth
    const btnBuffer = document.getElementById("btn-connect-buffer");
    if (btnBuffer) {
      btnBuffer.addEventListener("click", async () => {
        try {
          const res = await fetch("/api/v1/publishing/buffer/connect", { headers });
          const data = await res.json();
          if (res.ok && data.auth_url) {
            window.open(data.auth_url, "_blank");
          } else {
            alert(`Buffer Connect: ${data.detail || "Unable to generate OAuth URL"}`);
          }
        } catch (e) {
          alert(`Buffer Connect Error: ${e.message}`);
        }
      });
    }

    // Connect YouTube OAuth
    const btnYouTube = document.getElementById("btn-connect-youtube");
    if (btnYouTube) {
      btnYouTube.addEventListener("click", async () => {
        try {
          const res = await fetch("/api/v1/publishing/youtube/connect", { headers });
          const data = await res.json();
          if (res.ok && data.authorization_url) {
            window.open(data.authorization_url, "_blank");
          } else {
            alert(`YouTube Connect: ${data.detail || "Unable to generate OAuth URL"}`);
          }
        } catch (e) {
          alert(`YouTube Connect Error: ${e.message}`);
        }
      });
    }

    // Dispatch Due Publications
    const btnDispatch = document.getElementById("btn-dispatch-due");
    if (btnDispatch) {
      btnDispatch.addEventListener("click", async () => {
        btnDispatch.disabled = true;
        try {
          const res = await fetch("/api/v1/publishing/dispatch-due", { method: "POST", headers });
          const data = await res.json();
          alert(`Dispatched ${data.dispatched || 0} due items.`);
          loadPublishingData();
        } catch (e) {
          alert(`Dispatch Error: ${e.message}`);
        } finally {
          btnDispatch.disabled = false;
        }
      });
    }

    // Run 24h Daily Reconciliation
    const btnReconcile = document.getElementById("btn-reconcile-daily");
    if (btnReconcile) {
      btnReconcile.addEventListener("click", async () => {
        btnReconcile.disabled = true;
        btnReconcile.textContent = "Reconciling...";
        try {
          const res = await fetch("/api/v1/publishing/reconcile-daily?lookback_hours=24", { method: "POST", headers });
          const data = await res.json();
          alert(`Reconciliation finished: ${data.total_published} confirmed published, ${data.total_failed} failed.`);
          loadPublishingData();
        } catch (e) {
          alert(`Reconciliation Error: ${e.message}`);
        } finally {
          btnReconcile.disabled = false;
          btnReconcile.textContent = "Run 24h Daily Reconciliation";
        }
      });
    }

    

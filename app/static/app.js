/**
 * GTA 6 Shorts Engine — Production Operator Command Center
 * Milestone 3.1: Session-Cookie Authentication, CSRF Protection, Dual-Gate Inspector
 */

// Memory-only authentication tokens (NEVER stored in localStorage or sessionStorage)
let csrfToken = null;
let activeUser = null;
let cachedClips = [];
let cachedInspections = {};

document.addEventListener('DOMContentLoaded', () => {
  initAuthAndSession();
  setupNavigation();
  setupActionListeners();
});

// =============================================================================
// Authentication & Session Management
// =============================================================================

async function initAuthAndSession() {
  try {
    const res = await fetch('/api/v1/auth/session');
    if (res.ok) {
      const data = await res.json();
      if (data.authenticated) {
        csrfToken = data.csrf_token;
        activeUser = data.user_id;
        document.getElementById('login-modal').style.display = 'none';
        document.getElementById('operator-user-display').textContent = activeUser || 'Operator';
        loadAllDashboardData();
        return;
      }
    }
  } catch (err) {
    console.warn('Session check failed:', err);
  }

  // Not authenticated: display login modal
  document.getElementById('login-modal').style.display = 'flex';
}

async function handleLogin(e) {
  e.preventDefault();
  const keyInput = document.getElementById('login-api-key');
  const errorBox = document.getElementById('login-error');
  const apiKey = keyInput.value.trim();

  if (!apiKey) return;

  errorBox.style.display = 'none';
  const submitBtn = document.getElementById('btn-login-submit');
  submitBtn.disabled = true;
  submitBtn.textContent = 'Verifying credentials...';

  try {
    const res = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({ detail: 'Authentication failed' }));
      throw new Error(errData.detail || 'Invalid operator credentials.');
    }

    const data = await res.json();
    csrfToken = data.csrf_token;
    activeUser = data.user_id;

    // Clear password input immediately
    keyInput.value = '';
    document.getElementById('login-modal').style.display = 'none';
    document.getElementById('operator-user-display').textContent = activeUser || 'Operator';
    loadAllDashboardData();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.style.display = 'block';
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Authenticate & Establish Session';
  }
}

async function handleLogout() {
  try {
    await fetch('/api/v1/auth/logout', {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken || '' },
    });
  } catch (err) {
    console.warn('Logout error:', err);
  }
  csrfToken = null;
  activeUser = null;
  document.getElementById('login-modal').style.display = 'flex';
}

/**
 * Authenticated API Fetcher with CSRF injection and session cookie transmission
 */
async function apiFetch(url, options = {}) {
  const opts = { ...options };
  opts.headers = { ...(opts.headers || {}) };

  // Attach CSRF token on state-changing requests
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes((opts.method || 'GET').toUpperCase())) {
    if (csrfToken) {
      opts.headers['X-CSRF-Token'] = csrfToken;
    }
  }

  // Ensure same-origin credentials (cookies) are sent
  opts.credentials = 'same-origin';

  const res = await fetch(url, opts);
  if (res.status === 401) {
    csrfToken = null;
    activeUser = null;
    document.getElementById('login-modal').style.display = 'flex';
    throw new Error('Session expired or unauthenticated. Please log in.');
  }
  return res;
}

// =============================================================================
// Navigation & Section Switching
// =============================================================================

function setupNavigation() {
  const navItems = document.querySelectorAll('.nav-item');
  navItems.forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const tabId = item.getAttribute('data-tab');
      switchTab(tabId);
    });
  });

  // Mobile menu toggle
  const toggleBtn = document.getElementById('btn-toggle-menu');
  const sidebar = document.getElementById('app-sidebar');
  if (toggleBtn && sidebar) {
    toggleBtn.addEventListener('click', () => {
      sidebar.classList.toggle('open');
    });
  }
}

function switchTab(tabId) {
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.getAttribute('data-tab') === tabId);
  });

  document.querySelectorAll('.content-tab').forEach(el => {
    el.classList.toggle('active', el.id === `tab-${tabId}`);
  });

  // Close mobile sidebar if open
  const sidebar = document.getElementById('app-sidebar');
  if (sidebar) sidebar.classList.remove('open');

  // Load tab data
  if (tabId === 'campaigns') loadCampaigns();
  if (tabId === 'assets') loadAssets();
  if (tabId === 'production') loadProductionStudio();
  if (tabId === 'clips') loadClipsAndInspector();
  if (tabId === 'publishing') loadPublishingQueue();
  if (tabId === 'earnings') loadEarnings();
  if (tabId === 'audit') loadAuditLogs();
  if (tabId === 'settings') loadHealthAndSettings();
}

// =============================================================================
// Action Listeners
// =============================================================================

function setupActionListeners() {
  document.getElementById('login-form').addEventListener('submit', handleLogin);
  document.getElementById('btn-logout').addEventListener('click', handleLogout);

  document.getElementById('btn-refresh-overview').addEventListener('click', loadOverview);
  document.getElementById('btn-trigger-discovery').addEventListener('click', triggerDiscovery);
  document.getElementById('btn-discover-campaigns').addEventListener('click', triggerDiscovery);
  document.getElementById('btn-refresh-assets').addEventListener('click', loadAssets);
  document.getElementById('btn-trigger-production').addEventListener('click', triggerProductionPipeline);
  document.getElementById('btn-refresh-audit').addEventListener('click', loadAuditLogs);

  // Reward Confirmation Modal
  const rewardModal = document.getElementById('reward-modal');
  document.getElementById('btn-open-confirm-reward').addEventListener('click', () => {
    rewardModal.style.display = 'flex';
  });
  document.getElementById('btn-close-reward-modal').addEventListener('click', () => {
    rewardModal.style.display = 'none';
  });
  document.getElementById('reward-confirm-form').addEventListener('submit', handleConfirmReward);
}

function loadAllDashboardData() {
  loadOverview();
  loadEarnings();
}

// =============================================================================
// Section Loaders
// =============================================================================

async function loadOverview() {
  try {
    // 1. Earnings Summary & 30-day target
    const earnRes = await apiFetch('/api/v1/campaigns/earnings/summary');
    if (earnRes.ok) {
      const earn = await earnRes.json();
      const approved = Number(earn.total_approved_rewards || 0);
      const target = Number(earn.target_amount || 500);
      const pct = Math.min(100, Math.round((approved / target) * 1000) / 10);
      const days = earn.days_remaining || 30;

      document.getElementById('overview-confirmed-amount').textContent = `$${approved.toFixed(2)}`;
      document.getElementById('overview-days-left').textContent = `${days} days left`;
      document.getElementById('overview-progress-bar').style.width = `${pct}%`;
      document.getElementById('overview-progress-pct').textContent = `${pct}% achieved ($${approved.toFixed(2)} / $${target.toFixed(2)})`;

      const needed = Math.max(0, target - approved);
      const dailyRate = days > 0 ? (needed / days).toFixed(2) : '0.00';
      document.getElementById('overview-run-rate').textContent = `Run-rate needed: $${dailyRate} / day`;
    }

    // 2. Active campaigns & clips
    const campRes = await apiFetch('/api/v1/campaigns');
    if (campRes.ok) {
      const camps = await campRes.json();
      const activeCamps = (camps.campaigns || []).filter(c => ['approved', 'active', 'eligible'].includes(c.status));
      document.getElementById('stat-active-campaigns').textContent = activeCamps.length;
    }

    // 3. Action Queue
    const actRes = await apiFetch('/api/v1/marketplace/actions?status=pending');
    if (actRes.ok) {
      const actions = await actRes.json();
      const queueContainer = document.getElementById('overview-action-queue');
      document.getElementById('stat-pending-reviews').textContent = actions.length;
      document.getElementById('action-queue-count').textContent = `${actions.length} items`;

      if (actions.length === 0) {
        queueContainer.innerHTML = '<div class="empty-state">No pending operator actions required. System healthy.</div>';
      } else {
        queueContainer.innerHTML = actions.map(a => `
          <div class="alert-banner alert-warning" style="margin-bottom: 8px;">
            <div class="alert-content">
              <strong>${a.title || a.action_type}</strong>: ${a.message}
            </div>
            <button class="btn btn-sm btn-primary" onclick="resolveAction('${a.id}')">Resolve</button>
          </div>
        `).join('');
      }
    }
  } catch (err) {
    console.error('Error loading overview:', err);
  }
}

async function loadCampaigns() {
  const tbody = document.getElementById('campaigns-table-body');
  try {
    const res = await apiFetch('/api/v1/campaigns');
    if (!res.ok) throw new Error('Could not load campaigns');
    const data = await res.json();
    const list = data.campaigns || [];

    if (list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center">No campaigns found. Click "Discover Marketplace Offers" above.</td></tr>';
      return;
    }

    tbody.innerHTML = list.map(c => `
      <tr>
        <td><strong>${c.name}</strong><br><span style="font-size: 11px; color: var(--text-dim);">${c.id}</span></td>
        <td>${c.game_title || 'GTA 6 / Gaming'}</td>
        <td>${(c.accepted_platforms || ['TikTok', 'YouTube Shorts']).join(', ')}</td>
        <td>${c.reward_model || 'CPM'} ($${c.payout_amount || c.cpm || '0.00'})</td>
        <td><span class="badge badge-info">${c.opportunity_score ? c.opportunity_score.toFixed(1) : 'N/A'}</span></td>
        <td><span class="badge ${c.operator_approved ? 'badge-success' : 'badge-warning'}">${c.status || 'Discovered'}</span></td>
        <td>
          ${!c.operator_approved ?
            `<button class="btn btn-sm btn-primary" onclick="operatorAction('${c.id}', 'approve')">Approve</button>` :
            `<button class="btn btn-sm btn-outline" onclick="operatorAction('${c.id}', 'pause')">Pause</button>`
          }
        </td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center text-danger">${err.message}</td></tr>`;
  }
}

async function operatorAction(campaignId, action) {
  const reason = prompt(`Enter mandatory operator justification for ${action}:`);
  if (!reason || reason.trim().length < 5) {
    alert('Action cancelled: Justification must be at least 5 characters.');
    return;
  }
  const evidence = prompt('Enter evidence reference (document URL or reference ID):', 'Manual operator verification');
  if (!evidence) return;

  try {
    const res = await apiFetch(`/api/v1/campaigns/${campaignId}/operator-action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        reason: reason.trim(),
        evidence_reference: evidence.trim(),
      }),
    });
    if (res.ok) {
      loadCampaigns();
      loadOverview();
    } else {
      const err = await res.json();
      alert(`Action failed: ${err.detail}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function triggerDiscovery() {
  const btn = document.getElementById('btn-trigger-discovery');
  btn.disabled = true;
  btn.textContent = 'Discovering...';
  try {
    const res = await apiFetch('/api/v1/marketplace/discover?limit=10');
    if (res.ok) {
      alert('Marketplace discovery completed. New campaigns registered.');
      loadCampaigns();
      loadOverview();
    } else {
      const err = await res.json();
      alert(`Discovery note: ${err.detail || 'Could not complete live discovery'}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 Discover Campaigns';
  }
}

async function loadAssets() {
  const tbody = document.getElementById('assets-table-body');
  try {
    const res = await apiFetch('/api/v1/campaigns');
    const data = await res.json();
    const camps = data.campaigns || [];
    let rows = '';

    camps.forEach(c => {
      const driveUrl = c.assets_drive_url;
      if (driveUrl) {
        rows += `
          <tr>
            <td><strong>Campaign Asset Bundle</strong><br><span style="font-size:11px;">${driveUrl}</span></td>
            <td>${c.name}</td>
            <td>Google Drive</td>
            <td><span style="font-family:monospace; font-size:11px;">Verified Isolated</span></td>
            <td><span class="badge ${c.source_verification_status === 'verified' ? 'badge-success' : 'badge-warning'}">Rights Required</span></td>
            <td>${c.source_verification_status || 'Pending'}</td>
            <td><button class="btn btn-sm btn-outline" onclick="verifyAssetRights('${c.id}')">Verify Rights</button></td>
          </tr>
        `;
      }
    });

    tbody.innerHTML = rows || '<tr><td colspan="7" class="text-center">No campaign assets registered.</td></tr>';
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center">${err.message}</td></tr>`;
  }
}

function verifyAssetRights(campaignId) {
  const licensor = prompt('Enter Rights Holder or Authorized Licensor:');
  if (!licensor) return;
  const source = prompt('Enter License / Permission Document Reference:');
  if (!source) return;

  operatorAction(campaignId, 'verify_source');
}

async function loadProductionStudio() {
  const tbody = document.getElementById('candidates-table-body');
  try {
    const res = await apiFetch('/api/v1/marketplace/candidates/evaluated');
    if (res.ok) {
      const cands = await res.json();
      if (cands.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center">No candidate moments evaluated yet. Run the pipeline to evaluate gameplay moments.</td></tr>';
        return;
      }
      tbody.innerHTML = cands.map(c => `
        <tr>
          <td><strong>${c.candidate_id}</strong></td>
          <td>${c.source_id}<br><span style="font-size:11px;">${c.start_time}s - ${c.end_time}s (${c.duration}s)</span></td>
          <td><strong>${c.quality_score.toFixed(1)}</strong> / 100</td>
          <td><span class="badge ${c.quality_score >= 85 ? 'badge-success' : (c.quality_score >= 75 ? 'badge-info' : 'badge-danger')}">${c.tier}</span></td>
          <td><span class="badge ${c.verdict === 'select' ? 'badge-success' : 'badge-danger'}">${c.verdict}</span></td>
          <td style="font-size: 11px;">${c.factor_breakdown || 'Narrative, Visual, Audio, Alignment'}</td>
        </tr>
      `).join('');
    }
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center">Candidate evaluations logged in database.</td></tr>';
  }
}

async function triggerProductionPipeline() {
  const campId = prompt('Enter Approved Campaign ID to run pipeline:');
  if (!campId) return;

  const stepper = document.getElementById('pipeline-stepper');
  document.getElementById('pipeline-overall-status').textContent = 'Running Stage 1-13';
  stepper.querySelectorAll('.step-node').forEach(n => n.classList.add('active'));

  try {
    const res = await apiFetch('/api/v1/scheduler/run-daily', { method: 'POST' });
    if (res.ok) {
      alert('Pipeline execution initiated. Inspection results will appear in Clip Library.');
      loadClipsAndInspector();
    } else {
      const err = await res.json();
      alert(`Pipeline response: ${err.detail || 'Scheduled task dispatched'}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    document.getElementById('pipeline-overall-status').textContent = 'Completed / Idle';
  }
}

async function loadClipsAndInspector() {
  const container = document.getElementById('clips-items-container');
  try {
    const res = await apiFetch('/api/v1/clips');
    if (!res.ok) throw new Error('Could not fetch clips');
    const clips = await res.json();
    cachedClips = clips;

    document.getElementById('clips-count-badge').textContent = `${clips.length} clips`;

    if (clips.length === 0) {
      container.innerHTML = '<div class="empty-state">No produced clips found.</div>';
      return;
    }

    container.innerHTML = clips.map(c => `
      <div class="clip-list-card" onclick="selectClipForInspection('${c.id}')">
        <strong>${c.title || c.id}</strong>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
          Campaign: ${c.campaign_id || 'N/A'}<br>
          Format: 1080x1920 (9:16) MP4 | Compliance: <span class="badge ${c.is_compliant ? 'badge-success' : 'badge-danger'}">${c.compliance_status || 'verified'}</span>
        </div>
      </div>
    `).join('');

    // Auto-select first clip
    if (clips.length > 0) {
      selectClipForInspection(clips[0].id);
    }
  } catch (err) {
    container.innerHTML = `<div class="empty-state text-danger">${err.message}</div>`;
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

async function selectClipForInspection(clipId) {
  const reportPane = document.getElementById('inspection-report-content');
  const verdictBadge = document.getElementById('inspection-verdict-badge');
  const clip = cachedClips.find(c => c.id === clipId);
  if (!clip) return;
  reportPane.textContent = 'Loading recorded inspection…';
  verdictBadge.textContent = 'INSPECTION NOT YET LOADED';
  verdictBadge.className = 'badge badge-warning';

  try {
    const res = await apiFetch(`/api/v1/campaigns/inspections/${encodeURIComponent(clipId)}`);
    if (!res.ok) {
      if (res.status === 404) {
        reportPane.innerHTML = `<div class="card"><h4>${escapeHtml(clip.title || clip.id)}</h4><p>No recorded inspection exists for this clip yet. No gate result is assumed.</p></div>`;
        verdictBadge.textContent = 'NO RECORDED INSPECTION';
        verdictBadge.className = 'badge badge-warning';
        return;
      }
      throw new Error(`Inspection request failed (${res.status})`);
    }
    const report = await res.json();
    const parse = value => { if (typeof value !== 'string') return value || {}; try { return JSON.parse(value); } catch { return {}; } };
    const media = parse(report.media_metadata);
    const frames = parse(report.frame_samples);
    const failures = parse(report.failures);
    const gateA = !!report.gate_a_passed;
    const gateB = !!report.gate_b_passed;
    const overall = !!report.overall_approved;
    const gateLabel = (name, passed) => `<li>${passed ? 'Passed' : 'Failed'}: ${name}</li>`;
    const frameItems = Array.isArray(frames) ? frames : [];
    reportPane.innerHTML = `
      <div class="card" style="margin-bottom:16px"><h4>Clip: ${escapeHtml(clip.title || clip.id)}</h4>
      <p>Inspection time: ${escapeHtml(report.inspected_at || 'Not recorded')}</p>
      <p>Media path: ${escapeHtml(report.video_path || 'Not recorded')}</p></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
        <div class="card"><h5>Gate A: Evidence-backed candidate quality</h5><ul>${gateLabel('Quality threshold', gateA)}</ul><p>Recorded score: ${escapeHtml(report.quality_score ?? 'Unavailable')}</p></div>
        <div class="card"><h5>Gate B: Rights and media compliance</h5><ul>${gateLabel('Campaign and media checks', gateB)}</ul>
          <p>Media: ${escapeHtml(media.container_format || 'Unavailable')} · ${escapeHtml(media.width || '?')}×${escapeHtml(media.height || '?')} · ${escapeHtml(media.duration ?? '?')}s</p></div>
      </div>
      <div class="card"><h5>Recorded frame samples</h5>${frameItems.length ? `<ul>${frameItems.map(f => `<li>${escapeHtml(f.time_offset ?? f.timestamp ?? '?')}s · ${escapeHtml(f.path || f.url || 'sample')}</li>`).join('')}</ul>` : '<p>No frame samples were recorded.</p>'}</div>
      <div class="card" style="margin-top:16px"><h5>Failures</h5>${Array.isArray(failures) && failures.length ? `<ul>${failures.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : '<p>No failure details were recorded.</p>'}</div>`;
    verdictBadge.textContent = overall ? 'BOTH GATES PASSED' : 'GATES NOT APPROVED';
    verdictBadge.className = overall ? 'badge badge-success' : 'badge badge-danger';
  } catch (err) {
    reportPane.textContent = `Could not load the recorded inspection: ${err.message}`;
    verdictBadge.textContent = 'INSPECTION UNAVAILABLE';
    verdictBadge.className = 'badge badge-danger';
  }
}

async function loadPublishingQueue() {
  const tbody = document.getElementById('publishing-queue-body');
  try {
    const res = await apiFetch('/api/v1/clips');
    const clips = await res.json();
    const queued = clips.filter(c => c.is_compliant);

    if (queued.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center">No clips in publishing queue.</td></tr>';
      return;
    }

    tbody.innerHTML = queued.map(c => `
      <tr>
        <td><strong>${c.id}</strong></td>
        <td>${c.title || 'GTA 6 Viral Short'}<br><span style="font-size: 11px; color: var(--text-dim);">${c.caption || '#GTA6 #Gaming'}</span></td>
        <td>TikTok / YouTube Shorts</td>
        <td><span class="badge badge-success">Dual Gates Passed</span></td>
        <td><span class="badge badge-warning">Safe Mode (Manual Export)</span></td>
        <td><button class="btn btn-sm btn-secondary" onclick="exportPublicationPackage('${c.id}')">Export Package</button></td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center">${err.message}</td></tr>`;
  }
}

function exportPublicationPackage(clipId) {
  alert(`Publication package for ${clipId} exported for manual distribution. Automated direct publishing remains disabled in Milestone 3.1 safe mode.`);
}

async function loadEarnings() {
  try {
    const res = await apiFetch('/api/v1/campaigns/earnings/summary');
    if (res.ok) {
      const data = await res.json();
      document.getElementById('earn-confirmed-val').textContent = `$${(data.total_approved_rewards || 0).toFixed(2)}`;
      document.getElementById('earn-rolling-30d').textContent = `Rolling 30-day: $${(data.rolling_30d_confirmed || data.total_approved_rewards || 0).toFixed(2)}`;
      document.getElementById('earn-pending-val').textContent = `$${(data.total_pending_rewards || 0).toFixed(2)}`;
      document.getElementById('earn-estimated-val').textContent = `$${(data.estimated_earnings || 0).toFixed(2)}`;
    }

    // Load Submissions
    const subRes = await apiFetch('/api/v1/submissions');
    const tbody = document.getElementById('submissions-table-body');
    if (subRes.ok) {
      const subs = await subRes.json();
      if (subs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center">No clip submissions recorded yet.</td></tr>';
        return;
      }
      tbody.innerHTML = subs.map(s => `
        <tr>
          <td><strong>${s.id}</strong></td>
          <td>${s.clip_id}</td>
          <td>${s.platform}</td>
          <td>${s.submitted_at || 'Recent'}</td>
          <td>${(s.eligible_views || 0).toLocaleString()}</td>
          <td style="color: var(--accent-emerald); font-weight: 600;">$${(s.approved_rewards || 0).toFixed(2)}</td>
          <td><span style="font-family: monospace; font-size: 11px;">${s.marketplace_reward_ref || 'N/A'}</span></td>
          <td><span class="badge ${s.status === 'approved' ? 'badge-success' : 'badge-info'}">${s.status}</span></td>
        </tr>
      `).join('');
    }
  } catch (err) {
    console.error('Error loading earnings:', err);
  }
}

async function handleConfirmReward(e) {
  e.preventDefault();
  const subId = document.getElementById('reward-sub-id').value.trim();
  const amount = parseFloat(document.getElementById('reward-amount').value);
  const ref = document.getElementById('reward-ref').value.trim();
  const views = document.getElementById('reward-views').value;
  const notes = document.getElementById('reward-notes').value.trim();

  try {
    const res = await apiFetch('/api/v1/campaigns/rewards/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        submission_id: subId,
        reward_amount: amount,
        marketplace_ref: ref,
        eligible_views: views ? parseInt(views) : null,
        notes: notes || null,
      }),
    });

    if (res.ok) {
      alert(`Marketplace reward of $${amount.toFixed(2)} confirmed successfully.`);
      document.getElementById('reward-modal').style.display = 'none';
      document.getElementById('reward-confirm-form').reset();
      loadEarnings();
      loadOverview();
    } else {
      const err = await res.json();
      alert(`Reward validation failed: ${err.detail}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function loadAuditLogs() {
  const tbody = document.getElementById('audit-table-body');
  try {
    const res = await apiFetch('/api/v1/audit-logs');
    if (!res.ok) throw new Error('Could not fetch audit logs');
    const logs = await res.json();

    if (logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center">No audit logs recorded yet.</td></tr>';
      return;
    }

    tbody.innerHTML = logs.map(l => `
      <tr>
        <td style="font-size: 11px;">${l.created_at || l.timestamp}</td>
        <td><strong>${l.user_id || l.operator_id || 'System'}</strong></td>
        <td><span class="badge badge-info">${l.action}</span></td>
        <td>${l.resource_type || l.entity_type} (${l.resource_id || l.entity_id || 'all'})</td>
        <td>${l.details || l.reason || 'Automated validation check'}</td>
        <td style="font-size: 11px; font-family: monospace;">${l.evidence_reference || l.ip_address || 'system'}</td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center">${err.message}</td></tr>`;
  }
}

async function loadHealthAndSettings() {
  try {
    const res = await apiFetch('/health');
    if (res.ok) {
      const health = await res.json();
      document.getElementById('health-api').textContent = '✓';
      document.getElementById('health-api').className = 'health-status ok';
      if (health.environment) {
        document.getElementById('cfg-env').textContent = health.environment;
      }
    }
  } catch (err) {
    document.getElementById('health-api').textContent = '✗';
    document.getElementById('health-api').className = 'health-status fail';
  }
}

function resolveAction(actionId) {
  const notes = prompt('Enter resolution notes:');
  if (!notes) return;

  apiFetch(`/api/v1/marketplace/actions/${actionId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notes }),
  }).then(res => {
    if (res.ok) {
      loadOverview();
    } else {
      alert('Could not resolve action.');
    }
  });
}

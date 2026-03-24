RUNTIME_TASK_RAIL_CLIENT_SCRIPT = r"""
const sessionListEl = document.getElementById('sessionList');
const sessionDetailEl = document.getElementById('sessionDetail');
const summaryGridEl = document.getElementById('summaryGrid');
const opsGridEl = document.getElementById('opsGrid');
const traceStripEl = document.getElementById('traceStrip');
const metaRowEl = document.getElementById('metaRow');
const eventFeedEl = document.getElementById('eventFeed');
const pollStatusEl = document.getElementById('pollStatus');
const focusListEl = document.getElementById('focusList');
const artifactListEl = document.getElementById('artifactList');
const queryListEl = document.getElementById('queryList');
const selectedStepTitleEl = document.getElementById('selectedStepTitle');
const selectedStepBodyEl = document.getElementById('selectedStepBody');
const selectedStepMetaEl = document.getElementById('selectedStepMeta');
const traceRawPanelEl = document.getElementById('traceRawPanel');
const query = new URLSearchParams(window.location.search);
let selectedSessionId = query.get('session') || '';
let selectedEventSeq = 0;
let lastSeq = 0;
let knownEvents = [];
let sessions = [];

const statusClass = (value) => {
  const raw = String(value || 'running').toLowerCase();
  return ['running', 'completed', 'failed', 'pending_approval', 'interrupted', 'request_done', 'researching', 'solved'].includes(raw) ? raw : 'running';
};

const shortTime = (value) => {
  if (!value) return 'unknown';
  try {
    return new Date(value).toLocaleString();
  } catch (err) {
    return String(value);
  }
};

const formatNumber = (value) => {
  const num = Number(value || 0);
  if (!Number.isFinite(num)) return '0';
  return Math.abs(num) >= 100 ? String(Math.round(num)) : num.toFixed(1).replace(/\.0$/, '');
};

const escapeHtml = (value) => String(value || '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;');

function setQuerySession(sessionId) {
  const url = new URL(window.location.href);
  if (sessionId) url.searchParams.set('session', sessionId);
  else url.searchParams.delete('session');
  window.history.replaceState({}, '', url.toString());
}

function pickDefaultSession(items) {
  if (!items.length) return '';
  const runningOpenClaw = items.find((session) => String(session.session_id || '').startsWith('openclaw:') && String(session.status || '').toLowerCase() === 'running');
  if (runningOpenClaw) return runningOpenClaw.session_id || '';
  const anyOpenClaw = items.find((session) => String(session.session_id || '').startsWith('openclaw:'));
  if (anyOpenClaw) return anyOpenClaw.session_id || '';
  return items[0].session_id || '';
}

function buildSummary(session, events) {
  const artifactIds = new Set();
  const packetArtifactIds = new Set();
  const bundleArtifactIds = new Set();
  const candidateIds = new Set();
  const queryRuns = [];
  const startedQueries = new Set();
  const completedQueries = new Set();
  let topicId = '';
  let topicTitle = '';
  let claimId = '';
  let resultStatus = '';
  let activeStatus = String(session?.status || 'running');
  let lastMessage = String(session?.last_message || '');
  let latestTool = '';
  let postId = '';
  let queryCount = 0;
  let artifactCount = 0;
  let candidateCount = 0;
  let stopReason = '';
  const changedPaths = [];
  const failureItems = [];
  const artifactRows = [];
  const retryHistory = [];
  const toolAttempts = new Map();
  const stages = {
    received: false,
    claimed: false,
    packet: false,
    queries: false,
    bundle: false,
    result: false,
  };

  for (const event of events) {
    if (event.topic_id && !topicId) topicId = String(event.topic_id);
    if (event.topic_title) topicTitle = String(event.topic_title);
    if (event.claim_id) claimId = String(event.claim_id);
    if (event.result_status) resultStatus = String(event.result_status);
    if (event.post_id) postId = String(event.post_id);
    if (event.tool_name) latestTool = String(event.tool_name);
    if (event.message) lastMessage = String(event.message);
    if (event.status) activeStatus = String(event.status);
    if (!stopReason && event.stop_reason) stopReason = String(event.stop_reason);
    if (!stopReason && event.loop_stop_reason) stopReason = String(event.loop_stop_reason);
    if (!stopReason && event.final_stop_reason) stopReason = String(event.final_stop_reason);

    if (event.event_type === 'task_received') stages.received = true;
    if (event.claim_id || event.tool_name === 'hive.claim_task') stages.claimed = true;
    if (event.artifact_id) {
      artifactIds.add(String(event.artifact_id));
      artifactRows.push({
        artifactId: String(event.artifact_id),
        role: String(event.artifact_role || event.artifact_kind || 'artifact'),
        path: String(event.path || event.file_path || event.target_path || ''),
        toolName: String(event.tool_name || ''),
      });
      if (String(event.artifact_role || '') === 'packet' || String(event.tool_name || '') === 'liquefy.pack_research_packet') {
        packetArtifactIds.add(String(event.artifact_id));
      }
      if (String(event.artifact_role || '') === 'bundle' || String(event.tool_name || '') === 'liquefy.pack_research_bundle') {
        bundleArtifactIds.add(String(event.artifact_id));
      }
    }
    if (event.tool_name === 'liquefy.pack_research_packet') stages.packet = true;
    if (event.tool_name === 'liquefy.pack_research_bundle') stages.bundle = true;
    if (event.tool_name === 'hive.submit_result' || event.event_type === 'task_completed') stages.result = true;
    if (event.candidate_id) candidateIds.add(String(event.candidate_id));
    if (event.candidate_count != null) candidateCount = Math.max(candidateCount, Number(event.candidate_count) || 0);
    if (event.query_count != null) queryCount = Math.max(queryCount, Number(event.query_count) || 0);
    if (event.artifact_count != null) artifactCount = Math.max(artifactCount, Number(event.artifact_count) || 0);
    const changedPath = String(event.path || event.file_path || event.target_path || '').trim();
    if (changedPath && !changedPaths.includes(changedPath)) changedPaths.push(changedPath);
    const eventStatus = String(event.status || '').toLowerCase();
    const eventType = String(event.event_type || '').toLowerCase();
    if (eventType.includes('failed') || eventStatus === 'failed' || String(event.result_status || '').toLowerCase() === 'failed') {
      failureItems.push({
        type: String(event.event_type || 'failed'),
        tool: String(event.tool_name || ''),
        message: String(event.message || ''),
      });
    }
    if (event.retry_count != null) {
      retryHistory.push({
        tool: String(event.tool_name || 'runtime.step'),
        retryCount: Number(event.retry_count) || 0,
        reason: String(event.retry_reason || event.message || ''),
      });
    }
    if (event.tool_name) {
      toolAttempts.set(event.tool_name, Number(toolAttempts.get(event.tool_name) || 0) + 1);
    }

    if (event.tool_name === 'curiosity.run_external_topic') {
      const qIndex = Number(event.query_index || 0);
      const qTotal = Number(event.query_total || 0);
      const label = String(event.query || event.message || '').trim();
      const key = label || `${qIndex}/${qTotal}`;
      if (event.event_type === 'tool_started') startedQueries.add(key);
      if (event.event_type === 'tool_executed') completedQueries.add(key);
      if (label && !queryRuns.some((item) => item.label === label)) {
        queryRuns.push({
          label,
          index: qIndex,
          total: qTotal,
          state: event.event_type === 'tool_executed' ? 'completed' : 'running',
        });
      }
    }
  }

  const queryCompletedCount = completedQueries.size || queryCount;
  const queryStartedCount = Math.max(startedQueries.size, queryRuns.length, queryCompletedCount);
  if (queryStartedCount > 0 || queryCompletedCount > 0) stages.queries = true;
  artifactCount = Math.max(artifactCount, artifactIds.size);
  candidateCount = Math.max(candidateCount, candidateIds.size);
  for (const [toolName, attempts] of toolAttempts.entries()) {
    if (attempts > 1 && !retryHistory.some((item) => item.tool === toolName)) {
      retryHistory.push({
        tool: toolName,
        retryCount: attempts - 1,
        reason: 'repeated execution in the same session',
      });
    }
  }

  const title = topicTitle || session?.request_preview || session?.session_id || 'Recent runtime session';
  const requestStatus = String(session?.status || activeStatus || 'running').toLowerCase();
  const topicStatus = String(resultStatus || '').toLowerCase();
  const displayStatus = topicStatus || (String(session?.task_class || '').toLowerCase() === 'autonomous_research' && requestStatus === 'completed'
    ? 'request_done'
    : requestStatus || 'running');
  const requestStateLabel = requestStatus === 'completed' && topicStatus && topicStatus !== 'solved' && topicStatus !== 'completed'
    ? 'request finished; topic still active'
    : requestStatus === 'completed' && String(session?.task_class || '').toLowerCase() === 'autonomous_research'
      ? 'request finished after the first bounded pass'
      : requestStatus;
  return {
    sessionId: session?.session_id || '',
    title,
    requestPreview: String(session?.request_preview || ''),
    taskClass: String(session?.task_class || 'unknown'),
    status: displayStatus,
    requestStatus,
    requestStateLabel,
    topicStatus,
    lastMessage,
    updatedAt: String(session?.updated_at || ''),
    topicId,
    claimId,
    resultStatus: topicStatus || String(session?.status || activeStatus || ''),
    postId,
    latestTool,
    artifactIds: Array.from(artifactIds),
    packetArtifactIds: Array.from(packetArtifactIds),
    bundleArtifactIds: Array.from(bundleArtifactIds),
    candidateIds: Array.from(candidateIds),
    artifactRows,
    changedPaths,
    failureItems,
    retryHistory,
    stopReason: stopReason || (requestStatus === 'completed' ? 'bounded loop finished' : ''),
    queryRuns,
    queryStartedCount,
    queryCompletedCount,
    artifactCount,
    candidateCount,
    stages,
  };
}

function renderSessions() {
  if (!sessions.length) {
    sessionListEl.innerHTML = '<div class="empty-state">No recent runtime sessions yet.</div>';
    return;
  }
  sessionListEl.innerHTML = sessions.map((session) => {
    const active = session.session_id === selectedSessionId ? 'active' : '';
    const sessionSummary = buildSummary(session, session.session_id === selectedSessionId ? knownEvents : []);
    const badgeLabel = sessionSummary.status === 'request_done' ? 'request done' : (sessionSummary.status || 'running');
    return `
      <button class="session-card ${active}" data-session-id="${escapeHtml(session.session_id)}">
        <div class="session-top">
          <div class="session-id">${escapeHtml(session.session_id)}</div>
          <span class="badge ${statusClass(sessionSummary.status)}">${escapeHtml(badgeLabel)}</span>
        </div>
        <div class="session-preview">${escapeHtml(session.request_preview || session.last_message || 'Recent OpenClaw task')}</div>
        <div class="session-meta">
          <span>${escapeHtml(session.task_class || 'unknown')}</span>
          <span>${escapeHtml(String(session.event_count || 0))} events</span>
          ${session.resume_available ? `<span>resume ready from ${escapeHtml(String(session.checkpoint_step_count || 0))} step(s)</span>` : ''}
          <span>${escapeHtml(shortTime(session.updated_at))}</span>
        </div>
      </button>
    `;
  }).join('');
  sessionListEl.querySelectorAll('[data-session-id]').forEach((node) => {
    node.addEventListener('click', () => {
      const sessionId = node.getAttribute('data-session-id') || '';
      if (!sessionId || sessionId === selectedSessionId) return;
      selectedSessionId = sessionId;
      selectedEventSeq = 0;
      lastSeq = 0;
      knownEvents = [];
      setQuerySession(sessionId);
      renderSessions();
      renderSessionDetail();
      renderSummary();
      renderEvents();
      fetchEvents(true);
    });
  });
}

function renderSessionDetail() {
  const session = sessions.find((row) => row.session_id === selectedSessionId);
  if (!session) {
    sessionDetailEl.innerHTML = `
      <h2>No session selected</h2>
      <p>Pick a recent session from the left to inspect its runtime event trail.</p>
    `;
    return;
  }
  const summary = buildSummary(session, knownEvents);
  const detailLine = summary.status === 'researching'
    ? 'The chat request finished quickly because it only launched the first bounded research pass. The Hive topic is still researching in the background.'
    : 'This trace comes from real runtime session events, not fabricated chain-of-thought.';
  sessionDetailEl.innerHTML = `
    <h2>${escapeHtml(summary.title)}</h2>
    <p>${escapeHtml(summary.lastMessage || 'No event message yet.')} ${escapeHtml(detailLine)}</p>
  `;
}

function selectedEvent() {
  if (!knownEvents.length) return null;
  if (!selectedEventSeq) return knownEvents[knownEvents.length - 1] || null;
  return knownEvents.find((event) => Number(event.seq || 0) === Number(selectedEventSeq || 0)) || knownEvents[knownEvents.length - 1] || null;
}

function renderSelectedStep() {
  const session = sessions.find((row) => row.session_id === selectedSessionId);
  const event = selectedEvent();
  if (!session || !event) {
    selectedStepTitleEl.textContent = 'No step selected';
    selectedStepBodyEl.textContent = 'Pick a runtime event or let the latest step stay in focus.';
    selectedStepMetaEl.innerHTML = '';
    traceRawPanelEl.textContent = '';
    return;
  }
  const summary = buildSummary(session, knownEvents);
  const title = String(event.tool_name || event.event_type || 'runtime_step');
  const body = String(
    event.message
    || event.retry_reason
    || event.stop_reason
    || event.final_stop_reason
    || event.query
    || 'No step detail captured.',
  );
  const meta = [];
  meta.push(`<span class="meta-chip">seq ${escapeHtml(String(event.seq || '?'))}</span>`);
  meta.push(`<span class="meta-chip">type ${escapeHtml(String(event.event_type || 'status'))}</span>`);
  if (event.tool_name) meta.push(`<span class="meta-chip">tool ${escapeHtml(String(event.tool_name))}</span>`);
  if (event.status) meta.push(`<span class="meta-chip">status ${escapeHtml(String(event.status))}</span>`);
  if (event.path || event.file_path || event.target_path) {
    meta.push(`<span class="meta-chip">changed ${escapeHtml(String(event.path || event.file_path || event.target_path))}</span>`);
  }
  if (event.artifact_id) meta.push(`<span class="meta-chip">artifact ${escapeHtml(String(event.artifact_id))}</span>`);
  if (event.stop_reason || event.final_stop_reason || summary.stopReason) {
    meta.push(`<span class="meta-chip">stop ${escapeHtml(String(event.stop_reason || event.final_stop_reason || summary.stopReason))}</span>`);
  }
  if (event.retry_count != null) meta.push(`<span class="meta-chip">retry ${escapeHtml(String(event.retry_count))}</span>`);
  selectedStepTitleEl.textContent = title;
  selectedStepBodyEl.textContent = body;
  selectedStepMetaEl.innerHTML = meta.join('');
  traceRawPanelEl.textContent = JSON.stringify(
    {
      session: {
        session_id: session.session_id,
        request_preview: session.request_preview,
        status: session.status,
        task_class: session.task_class,
      },
      summary,
      selected_event: event,
    },
    null,
    2,
  );
}

function renderSummary() {
  const session = sessions.find((row) => row.session_id === selectedSessionId);
  if (!session) {
    summaryGridEl.innerHTML = '<div class="empty-state">No session summary yet.</div>';
    traceStripEl.innerHTML = '<div class="empty-state">No process rail yet.</div>';
    metaRowEl.innerHTML = '';
    focusListEl.innerHTML = '<div class="inspector-item">No topic or claim selected yet.</div>';
    artifactListEl.innerHTML = '<div class="inspector-item">No packed artifacts yet.</div>';
    queryListEl.innerHTML = '<div class="inspector-item">No query runs yet.</div>';
    opsGridEl.innerHTML = '<div class="empty-state">No stop, failure, or retry state yet.</div>';
    traceRawPanelEl.textContent = '';
    return;
  }
  const summary = buildSummary(session, knownEvents);
  const stats = [
    { label: 'request state', value: summary.requestStatus || 'running' },
    { label: 'topic', value: summary.topicId ? `#${summary.topicId.slice(0, 8)}` : 'none yet' },
    { label: 'claim', value: summary.claimId ? summary.claimId.slice(0, 8) : 'none yet' },
    { label: 'queries', value: `${summary.queryCompletedCount}/${Math.max(summary.queryStartedCount, summary.queryCompletedCount)}` },
    { label: 'artifacts', value: String(summary.artifactCount || summary.artifactIds.length || 0) },
    { label: 'topic state', value: summary.resultStatus || summary.status || 'running' },
  ];
  summaryGridEl.innerHTML = stats.map((item) => `
    <div class="stat-card">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
    </div>
  `).join('');

  const stageRows = [
    { key: 'received', label: 'Request', value: summary.stages.received ? 'accepted' : 'waiting', detail: session.request_preview || summary.title },
    { key: 'claimed', label: 'Claim', value: summary.stages.claimed ? (summary.claimId ? summary.claimId.slice(0, 8) : 'active') : 'not claimed', detail: summary.topicId ? `topic #${summary.topicId.slice(0, 8)}` : 'no live topic yet' },
    { key: 'packet', label: 'Packet', value: summary.stages.packet ? 'packed' : 'pending', detail: summary.packetArtifactIds.length ? summary.packetArtifactIds.map((item) => item.slice(0, 8)).join(', ') : 'machine-readable packet not packed yet' },
    { key: 'queries', label: 'Queries', value: `${summary.queryCompletedCount}`, detail: summary.queryStartedCount ? `${summary.queryCompletedCount}/${summary.queryStartedCount} bounded runs finished` : 'no bounded research runs yet' },
    { key: 'bundle', label: 'Artifacts', value: `${summary.artifactCount || summary.artifactIds.length}`, detail: summary.bundleArtifactIds.length ? `bundle ${summary.bundleArtifactIds.map((item) => item.slice(0, 8)).join(', ')}` : 'no research bundle yet' },
    { key: 'result', label: 'Topic', value: summary.resultStatus || summary.status || 'running', detail: summary.postId ? `post ${summary.postId.slice(0, 8)}` : (summary.lastMessage || 'no result post yet') },
  ];
  traceStripEl.innerHTML = stageRows.map((stage) => {
    const stateClass = summary.status === 'failed' && stage.key === 'result'
      ? 'failed'
      : summary.stages[stage.key]
        ? 'done'
        : stage.key === 'queries' && summary.queryStartedCount > 0
          ? 'active'
          : '';
    return `
      <article class="trace-stage ${stateClass}">
        <div class="stage-label">${escapeHtml(stage.label)}</div>
        <div class="stage-value">${escapeHtml(stage.value)}</div>
        <div class="stage-detail">${escapeHtml(stage.detail)}</div>
      </article>
    `;
  }).join('');

  const meta = [];
  meta.push(`<span class="meta-chip">request ${escapeHtml(summary.requestStateLabel || summary.requestStatus || 'running')}</span>`);
  meta.push(`<span class="meta-chip">topic ${escapeHtml(summary.resultStatus || summary.status || 'running')}</span>`);
  meta.push(`<span class="meta-chip">class ${escapeHtml(session.task_class || 'unknown')}</span>`);
  meta.push(`<span class="meta-chip">events ${escapeHtml(String(session.event_count || knownEvents.length || 0))}</span>`);
  meta.push(`<span class="meta-chip">updated ${escapeHtml(shortTime(session.updated_at))}</span>`);
  if (summary.latestTool) meta.push(`<span class="meta-chip">tool ${escapeHtml(summary.latestTool)}</span>`);
  if (summary.topicId) meta.push(`<span class="meta-chip">topic ${escapeHtml(summary.topicId)}</span>`);
  if (summary.stopReason) meta.push(`<span class="meta-chip">stop ${escapeHtml(summary.stopReason)}</span>`);
  metaRowEl.innerHTML = meta.join('');

  const focusItems = [];
  if (summary.topicId) focusItems.push(`<div class="inspector-item">Topic <code>${escapeHtml(summary.topicId)}</code></div>`);
  if (summary.claimId) focusItems.push(`<div class="inspector-item">Claim <code>${escapeHtml(summary.claimId)}</code></div>`);
  focusItems.push(`<div class="inspector-item">Request state: <code>${escapeHtml(summary.requestStatus || 'running')}</code></div>`);
  focusItems.push(`<div class="inspector-item">Topic state: <code>${escapeHtml(summary.resultStatus || summary.status || 'running')}</code></div>`);
  if (summary.stopReason) focusItems.push(`<div class="inspector-item">Stop reason: <code>${escapeHtml(summary.stopReason)}</code></div>`);
  if (summary.changedPaths.length) {
    summary.changedPaths.slice(0, 6).forEach((path) => {
      focusItems.push(`<div class="inspector-item">Changed <code>${escapeHtml(path)}</code></div>`);
    });
  }
  if (summary.postId) focusItems.push(`<div class="inspector-item">Last result post <code>${escapeHtml(summary.postId)}</code></div>`);
  if (!focusItems.length) focusItems.push('<div class="inspector-item">No topic or claim selected yet.</div>');
  focusListEl.innerHTML = focusItems.join('');

  const artifactItems = summary.artifactRows.map((item) => {
    const role = summary.packetArtifactIds.includes(item.artifactId) ? 'packet' : summary.bundleArtifactIds.includes(item.artifactId) ? 'bundle' : item.role || 'artifact';
    const tail = item.path ? ` · <code>${escapeHtml(item.path)}</code>` : item.toolName ? ` · ${escapeHtml(item.toolName)}` : '';
    return `<div class="inspector-item">${escapeHtml(role)} <code>${escapeHtml(item.artifactId)}</code>${tail}</div>`;
  });
  artifactListEl.innerHTML = artifactItems.length ? artifactItems.join('') : '<div class="inspector-item">No packed artifacts yet.</div>';

  const queryItems = [];
  summary.retryHistory.forEach((item) => {
    queryItems.push(`<div class="inspector-item">retry <code>${escapeHtml(item.tool)}</code> × ${escapeHtml(String(item.retryCount || 0))}${item.reason ? ` · ${escapeHtml(item.reason)}` : ''}</div>`);
  });
  summary.queryRuns.forEach((item) => {
    const prefix = item.total ? `${item.index}/${item.total}` : 'query';
    queryItems.push(`<div class="inspector-item"><code>${escapeHtml(prefix)}</code> ${escapeHtml(item.label)}</div>`);
  });
  queryListEl.innerHTML = queryItems.length ? queryItems.join('') : '<div class="inspector-item">No query runs yet.</div>';

  const failureCount = summary.failureItems.length;
  const retryCount = summary.retryHistory.reduce((total, item) => total + Number(item.retryCount || 0), 0);
  const changedCount = summary.changedPaths.length;
  const opsCards = [
    {
      tone: summary.stopReason ? 'good' : '',
      label: 'Stop reason',
      value: summary.stopReason || 'still running',
      detail: 'Bounded execution stops explicitly instead of pretending work is still in flight.',
    },
    {
      tone: failureCount ? 'bad' : 'good',
      label: 'Failures',
      value: String(failureCount),
      detail: failureCount
        ? summary.failureItems.slice(0, 2).map((item) => item.message || item.type).join(' | ')
        : 'No failed runtime steps in this session.',
    },
    {
      tone: retryCount ? 'warn' : '',
      label: 'Retries',
      value: String(retryCount),
      detail: retryCount
        ? summary.retryHistory.slice(0, 2).map((item) => `${item.tool} x${item.retryCount}`).join(' | ')
        : 'No repeated runtime retries.',
    },
    {
      tone: changedCount ? 'good' : '',
      label: 'Changed',
      value: String(changedCount),
      detail: changedCount
        ? summary.changedPaths.slice(0, 2).join(' | ')
        : 'No file or target path changes recorded.',
    },
  ];
  opsGridEl.innerHTML = opsCards.map((item) => `
    <article class="ops-card ${escapeHtml(item.tone)}">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      <p>${escapeHtml(item.detail)}</p>
    </article>
  `).join('');
}

function renderEvents() {
  if (!knownEvents.length) {
    eventFeedEl.innerHTML = '<div class="empty-state">No runtime events for this session yet.</div>';
    return;
  }
  const activeSeq = Number((selectedEvent() || {}).seq || 0);
  eventFeedEl.innerHTML = knownEvents.map((event) => {
    const chips = [];
    if (event.seq != null) chips.push(`<span class="meta-chip">seq ${escapeHtml(String(event.seq))}</span>`);
    if (event.topic_id) chips.push(`<span class="meta-chip">topic ${escapeHtml(String(event.topic_id).slice(0, 8))}</span>`);
    if (event.claim_id) chips.push(`<span class="meta-chip">claim ${escapeHtml(String(event.claim_id).slice(0, 8))}</span>`);
    if (event.artifact_id) chips.push(`<span class="meta-chip">artifact ${escapeHtml(String(event.artifact_id).slice(0, 8))}</span>`);
    if (event.query_index || event.query_total) chips.push(`<span class="meta-chip">query ${escapeHtml(String(event.query_index || 0))}/${escapeHtml(String(event.query_total || 0))}</span>`);
    if (event.candidate_id) chips.push(`<span class="meta-chip">candidate ${escapeHtml(String(event.candidate_id).slice(0, 8))}</span>`);
    if (event.result_status) chips.push(`<span class="meta-chip">result ${escapeHtml(String(event.result_status))}</span>`);
    if (event.tool_name) chips.push(`<span class="meta-chip">tool ${escapeHtml(String(event.tool_name))}</span>`);
    if (event.status) chips.push(`<span class="meta-chip">status ${escapeHtml(String(event.status))}</span>`);
    if (event.stop_reason) chips.push(`<span class="meta-chip">stop ${escapeHtml(String(event.stop_reason))}</span>`);
    if (event.retry_count != null) chips.push(`<span class="meta-chip">retry ${escapeHtml(String(event.retry_count))}</span>`);
    const activeClass = Number(event.seq || 0) === activeSeq ? 'is-active' : '';
    return `
      <article class="event-card ${escapeHtml(String(event.event_type || 'status'))} ${activeClass}" data-event-seq="${escapeHtml(String(event.seq || '0'))}">
        <div class="event-head">
          <div class="event-type">${escapeHtml(event.event_type || 'status')}</div>
          <div class="event-time">${escapeHtml(shortTime(event.created_at))}</div>
        </div>
        <div class="event-message">${escapeHtml(event.message || '')}</div>
        <div class="event-meta">${chips.join('')}</div>
      </article>
    `;
  }).join('');
  eventFeedEl.querySelectorAll('[data-event-seq]').forEach((node) => {
    node.addEventListener('click', () => {
      selectedEventSeq = Number(node.getAttribute('data-event-seq') || '0') || 0;
      renderEvents();
      renderSelectedStep();
    });
  });
}

async function fetchSessions() {
  pollStatusEl.textContent = 'polling';
  const response = await fetch('/api/runtime/sessions');
  if (!response.ok) {
    pollStatusEl.textContent = 'error';
    return;
  }
  const payload = await response.json();
  sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
  sessions.sort((a, b) => {
    const aOpen = String(a.session_id || '').startsWith('openclaw:') ? 1 : 0;
    const bOpen = String(b.session_id || '').startsWith('openclaw:') ? 1 : 0;
    if (aOpen !== bOpen) return bOpen - aOpen;
    return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
  });
  if (!selectedSessionId && sessions.length) {
    selectedSessionId = pickDefaultSession(sessions);
    setQuerySession(selectedSessionId);
  }
  if (selectedSessionId && !sessions.some((row) => row.session_id === selectedSessionId)) {
    selectedSessionId = pickDefaultSession(sessions);
    lastSeq = 0;
    knownEvents = [];
    setQuerySession(selectedSessionId);
  }
  renderSessions();
  renderSessionDetail();
  renderSummary();
}

async function fetchEvents(reset = false) {
  if (!selectedSessionId) {
    renderEvents();
    return;
  }
  const after = reset ? 0 : lastSeq;
  const response = await fetch(`/api/runtime/events?session=${encodeURIComponent(selectedSessionId)}&after=${after}&limit=120`);
  if (!response.ok) {
    pollStatusEl.textContent = 'error';
    return;
  }
  const payload = await response.json();
  const incoming = Array.isArray(payload.events) ? payload.events : [];
  if (reset) {
    knownEvents = incoming;
  } else if (incoming.length) {
    knownEvents = knownEvents.concat(incoming);
  }
  if (knownEvents.length) {
    const selectedStillExists = knownEvents.some((event) => Number(event.seq || 0) === Number(selectedEventSeq || 0));
    if (!selectedStillExists) {
      selectedEventSeq = Number(knownEvents[knownEvents.length - 1].seq || 0) || 0;
    }
  } else {
    selectedEventSeq = 0;
  }
  if (payload.next_after != null) {
    lastSeq = Number(payload.next_after) || lastSeq;
  } else if (knownEvents.length) {
    lastSeq = Number(knownEvents[knownEvents.length - 1].seq || 0) || lastSeq;
  }
  renderSessionDetail();
  renderSummary();
  renderEvents();
  renderSelectedStep();
  pollStatusEl.textContent = 'live';
}

async function tick() {
  try {
    await fetchSessions();
    await fetchEvents();
  } catch (err) {
    pollStatusEl.textContent = 'error';
  }
}

tick();
setInterval(tick, 1200);
"""

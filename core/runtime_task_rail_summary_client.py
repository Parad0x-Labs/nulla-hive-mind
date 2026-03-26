RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT = r"""
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
    if (event.tool_name || event.intent) latestTool = String(event.tool_name || event.intent);
    if (event.message) lastMessage = String(event.message);
    if (event.status) activeStatus = String(event.status);
    if (!stopReason && event.stop_reason) stopReason = String(event.stop_reason);
    if (!stopReason && event.loop_stop_reason) stopReason = String(event.loop_stop_reason);
    if (!stopReason && event.final_stop_reason) stopReason = String(event.final_stop_reason);

    if (event.event_type === 'task_received' || event.event_type === 'task_envelope_started') stages.received = true;
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
    if (event.tool_name === 'hive.submit_result' || event.event_type === 'task_completed' || event.event_type === 'task_envelope_completed' || event.event_type === 'task_envelope_merge_completed') stages.result = true;
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
"""

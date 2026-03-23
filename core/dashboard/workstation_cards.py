from __future__ import annotations

WORKSTATION_CARD_RENDERERS = """    function extractEvidenceKinds(post) {
      const direct = Array.isArray(post?.evidence_kinds) ? post.evidence_kinds.filter(Boolean) : [];
      if (direct.length) return direct.slice(0, 6);
      const refs = Array.isArray(post?.evidence_refs) ? post.evidence_refs : [];
      return refs
        .map((ref) => String(ref?.kind || ref?.type || '').trim())
        .filter(Boolean)
        .slice(0, 6);
    }

    function buildTradingEvidenceSummary(post) {
      const refs = Array.isArray(post?.evidence_refs) ? post.evidence_refs : [];
      if (!refs.length) return null;
      const evidenceKinds = extractEvidenceKinds(post);
      let summary = null;
      let heartbeat = null;
      let decision = null;
      let lab = null;
      let callCount = null;
      let athCount = null;
      let lessonCount = null;
      let missedCount = null;
      let discoveryCount = null;
      for (const ref of refs) {
        const kind = String(ref?.kind || ref?.type || '').trim().toLowerCase();
        if (kind === 'trading_learning_summary' && ref?.summary) summary = ref.summary;
        if (kind === 'trading_runtime_heartbeat' && ref?.heartbeat) heartbeat = ref.heartbeat;
        if (kind === 'trading_decision_funnel' && ref?.summary) decision = ref.summary;
        if (kind === 'trading_learning_lab_summary' && ref?.summary) lab = ref.summary;
        if (kind === 'trading_calls') callCount = Array.isArray(ref?.items) ? ref.items.length : 0;
        if (kind === 'trading_ath_updates') athCount = Array.isArray(ref?.items) ? ref.items.length : 0;
        if (kind === 'trading_lessons') lessonCount = Array.isArray(ref?.items) ? ref.items.length : 0;
        if (kind === 'trading_missed_mooners') missedCount = Array.isArray(ref?.items) ? ref.items.length : 0;
        if (kind === 'trading_discoveries') discoveryCount = Array.isArray(ref?.items) ? ref.items.length : 0;
      }
      if (missedCount === null && lab && Number.isFinite(Number(lab.missed_opportunities))) {
        missedCount = Number(lab.missed_opportunities);
      }
      if (discoveryCount === null && lab && Number.isFinite(Number(lab.discoveries))) {
        discoveryCount = Number(lab.discoveries);
      }
      const hasTradingSignal = summary || heartbeat || decision || lab || callCount !== null || athCount !== null || lessonCount !== null || missedCount !== null || discoveryCount !== null;
      if (!hasTradingSignal) return null;
      const lines = [];
      if (summary) {
        lines.push(
          `calls ${fmtNumber(summary.total_calls || 0)} · wins ${fmtNumber(summary.wins || 0)} · losses ${fmtNumber(summary.losses || 0)} · pending ${fmtNumber(summary.pending || 0)} · safe ${fmtPct(summary.safe_exit_pct || 0)}`
        );
      }
      if (heartbeat) {
        lines.push(
          `scanner ${heartbeat.signal_only ? 'signal-only' : 'live'} · tick ${fmtNumber(heartbeat.tick || 0)} · tracked ${fmtNumber(heartbeat.tracked_tokens || 0)} · new ${fmtNumber(heartbeat.new_tokens_seen || 0)} · ${String(heartbeat.market_regime || 'UNKNOWN')}`
        );
      }
      if (decision) {
        lines.push(
          `funnel pass ${fmtNumber(decision.pass || 0)} · reject ${fmtNumber(decision.buy_rejected || 0)} · buy ${fmtNumber(decision.buy || 0)}`
        );
      }
      if (lab) {
        lines.push(
          `learn ${fmtNumber(lab.token_learnings || 0)} · missed ${fmtNumber(lab.missed_opportunities || 0)} · discoveries ${fmtNumber(lab.discoveries || 0)} · patterns ${fmtNumber(lab.mined_patterns || 0)}`
        );
      }
      const counters = [
        callCount != null ? `new calls ${fmtNumber(callCount)}` : '',
        athCount != null ? `ath updates ${fmtNumber(athCount)}` : '',
        lessonCount != null ? `lessons ${fmtNumber(lessonCount)}` : '',
        missedCount != null ? `missed ${fmtNumber(missedCount)}` : '',
        discoveryCount != null ? `discoveries ${fmtNumber(discoveryCount)}` : '',
      ].filter(Boolean);
      if (counters.length) lines.push(counters.join(' · '));
      const title = normalizeInlineText(post?.topic_title || post?.post_kind || 'trading update');
      return {
        title,
        preview: lines.slice(0, 2).join(' | ') || 'Structured trading update.',
        body: lines.join('\\n') || 'Structured trading update.',
        evidenceKinds,
      };
    }

    function compactText(value, maxLen = 180) {
      const text = normalizeInlineText(value);
      if (!text) return '';
      if (text.length <= maxLen) return text;
      return `${text.slice(0, Math.max(0, maxLen - 1)).trimEnd()}…`;
    }

    function postHeadline(post) {
      const structured = buildTradingEvidenceSummary(post);
      if (structured?.title) return structured.title;
      const raw = String(post?.body || post?.detail || '');
      const firstLine = normalizeInlineText(raw.split(/\\n+/)[0] || '');
      if (firstLine && firstLine.length <= 84) return firstLine;
      const kind = normalizeInlineText(post?.post_kind || post?.kind || 'update');
      const token = normalizeInlineText(post?.token_name || '');
      if (token) return `${kind} · ${token}`;
      const topic = normalizeInlineText(post?.topic_title || '');
      if (topic) return `${kind} · ${topic}`;
      return kind || 'update';
    }

    function postPreview(post, maxLen = 180) {
      const structured = buildTradingEvidenceSummary(post);
      if (structured?.preview) return compactText(structured.preview, maxLen);
      const raw = normalizeInlineText(post?.body || post?.detail || '');
      if (!raw) return 'No detail yet.';
      const headline = normalizeInlineText(postHeadline(post));
      const trimmed = raw.startsWith(headline)
        ? raw.slice(headline.length).replace(/^[\\s.:-]+/, '')
        : raw;
      return compactText(trimmed || raw, maxLen) || 'No detail yet.';
    }

    function renderCompactPostCard(post, options = {}) {
      const structured = buildTradingEvidenceSummary(post);
      const createdAt = post?.created_at || post?.ts || post?.timestamp || 0;
      const author = post?.author_label || post?.author_claim_label || post?.author_display_name || shortId(post?.author_agent_id || '', 18) || 'unknown';
      const topic = normalizeInlineText(post?.topic_title || '');
      const body = String(structured?.body || post?.body || post?.detail || '').trim() || 'No detail yet.';
      const evidenceKinds = structured?.evidenceKinds || extractEvidenceKinds(post);
      const commonsMeta = post?.commons_meta || {};
      const promotion = commonsMeta?.promotion_candidate || null;
      const href = post?.topic_id ? topicHref(post.topic_id) : '';
      const previewLen = Number(options.previewLen || 180);
      const detailKey = openKey('post', post?.post_id || '', post?.topic_id || '', createdAt, structured?.title || postHeadline(post));
      const inspectPayload = {
        post_id: post?.post_id || '',
        topic_id: post?.topic_id || '',
        title: structured?.title || postHeadline(post),
        summary: structured?.preview || postPreview(post, previewLen),
        body,
        source_label: 'watcher-derived',
        freshness: 'current',
        status: post?.post_kind || post?.kind || 'update',
        topic_title: topic,
        author,
        created_at: createdAt,
        evidence_kinds: evidenceKinds,
      };
      return `
        <details class="fold-card" data-open-key="${esc(detailKey)}" ${inspectAttrs('Observation', structured?.title || postHeadline(post), inspectPayload)}${options.defaultOpen ? ' open' : ''}>
          <summary>
            <div class="fold-title-row">
              <div class="fold-title">${esc(structured?.title || postHeadline(post))}</div>
              <div class="fold-stamp">${fmtTime(createdAt)}</div>
            </div>
            <div class="fold-preview">${esc(structured?.preview || postPreview(post, previewLen))}</div>
            <div class="row-meta">
              ${chip(post?.post_kind || post?.kind || 'update')}
              ${post?.stance ? chip(post.stance) : ''}
              ${post?.call_status ? chip(post.call_status, post.call_status === 'WIN' ? 'ok' : (post.call_status === 'LOSS' ? 'warn' : '')) : ''}
              ${commonsMeta?.support_weight ? chip(`support ${Number(commonsMeta.support_weight || 0).toFixed(1)}`, 'ok') : ''}
              ${commonsMeta?.comment_count ? chip(`${fmtNumber(commonsMeta.comment_count || 0)} comments`) : ''}
              ${promotion ? chip(`promotion ${promotion.status || 'draft'}`, promotion.status === 'approved' || promotion.status === 'promoted' ? 'ok' : '') : ''}
              ${topic ? `<span>${esc(topic)}</span>` : ''}
              <span>${esc(author)}</span>
            </div>
          </summary>
          <div class="fold-body">
            <div class="body-pre">${esc(body)}</div>
            <div class="row-meta">
              ${evidenceKinds.map((kind) => chip(kind)).join('')}
              ${commonsMeta?.challenge_weight ? chip(`challenge ${Number(commonsMeta.challenge_weight || 0).toFixed(1)}`, 'warn') : ''}
              ${promotion ? chip(`score ${Number(promotion.score || 0).toFixed(2)}`) : ''}
              ${promotion?.review_state ? chip(`review ${promotion.review_state}`) : ''}
              <button class="inspect-button" type="button" ${inspectAttrs('Observation', structured?.title || postHeadline(post), inspectPayload)}>Inspect</button>
              ${href && options.topicLink !== false ? `<a class="copy-button" href="${href}">Open topic</a>` : ''}
            </div>
          </div>
        </details>
      `;
    }

    function renderCompactPostList(posts, options = {}) {
      const items = Array.isArray(posts) ? posts : [];
      if (!items.length) {
        return `<div class="empty">${esc(options.emptyText || 'No posts yet.')}</div>`;
      }
      const limit = Math.max(1, Number(options.limit || 8));
      const visible = items.slice(0, limit);
      const note = items.length > limit
        ? `<div class="list-note">Showing latest ${fmtNumber(visible.length)} of ${fmtNumber(items.length)} posts.</div>`
        : '';
      return `${note}${visible.map((post, index) => renderCompactPostCard(post, {
        previewLen: options.previewLen || 180,
        topicLink: options.topicLink,
        defaultOpen: Boolean(options.defaultOpenFirst && index === 0),
      })).join('')}`;
    }

    function taskEventLabel(eventType) {
      const normalized = String(eventType || '').toLowerCase();
      return {
        topic_created: 'topic_opened',
        task_claimed: 'claimed',
        task_released: 'released',
        task_completed: 'claim_done',
        task_blocked: 'blocked',
        progress_update: 'progress',
        evidence_added: 'evidence',
        challenge_raised: 'challenge',
        summary_posted: 'summary',
        result_submitted: 'result',
      }[normalized] || (normalized || 'event');
    }

    function taskEventKind(eventType) {
      const normalized = String(eventType || '').toLowerCase();
      if (normalized === 'task_completed' || normalized === 'result_submitted') return 'ok';
      if (normalized === 'task_blocked' || normalized === 'challenge_raised') return 'warn';
      return '';
    }

    function taskEventPreview(event) {
      const parts = [];
      if (event.agent_label) parts.push(event.agent_label);
      const detail = compactText(event.detail || '', 120);
      if (detail) parts.push(detail);
      return parts.join(' | ') || 'No task summary yet.';
    }

    function renderTaskEventFold(event) {
      const detailKey = openKey('task-event', event.topic_id || event.topic_title || '', event.timestamp || '', event.event_type || '', event.claim_id || event.agent_label || '');
      const inspectPayload = {
        topic_id: event.topic_id || '',
        title: event.topic_title || 'Hive task event',
        summary: taskEventPreview(event),
        detail: event.detail || '',
        truth_label: 'watcher-derived',
        freshness: event.presence_freshness || 'current',
        status: event.status || event.event_type || '',
        claim_id: event.claim_id || '',
        agent_label: event.agent_label || '',
        timestamp: event.timestamp || '',
        tags: event.tags || [],
        capability_tags: event.capability_tags || [],
        conflict_count: event.event_type === 'challenge_raised' || event.event_type === 'task_blocked' ? 1 : 0,
      };
      return `
        <details class="fold-card" data-open-key="${esc(detailKey)}" ${inspectAttrs('Observation', event.topic_title || 'Hive task event', inspectPayload)}>
          <summary>
            <div class="fold-title-row">
              <h3 class="fold-title">${esc(event.topic_title || 'Hive task event')}</h3>
              <div class="fold-stamp">${fmtTime(event.timestamp)}</div>
            </div>
            <p class="fold-preview">${esc(taskEventPreview(event))}</p>
            <div class="row-meta">
              ${chip(taskEventLabel(event.event_type), taskEventKind(event.event_type))}
              ${event.progress_state ? chip(event.progress_state, event.progress_state === 'blocked' ? 'warn' : '') : ''}
              ${event.status ? chip(event.status, event.status === 'solved' || event.status === 'completed' ? 'ok' : '') : ''}
            </div>
          </summary>
          <div class="fold-body">
            <p class="body-pre">${esc(event.detail || 'No task detail provided.')}</p>
            <div class="row-meta">
              <span>${esc(event.agent_label || 'unknown')}</span>
              ${event.claim_id ? `<span class="mono">${esc(shortId(event.claim_id, 16))}</span>` : ''}
              ${(event.tags || []).slice(0, 4).map((tag) => chip(tag)).join('')}
              ${(event.capability_tags || []).slice(0, 4).map((tag) => chip(tag)).join('')}
              <button class="inspect-button" type="button" ${inspectAttrs('Observation', event.topic_title || 'Hive task event', inspectPayload)}>Inspect</button>
            </div>
            ${event.topic_id ? `<div class="row-meta"><a class="copy-button" href="${topicHref(event.topic_id)}">Open topic</a></div>` : ''}
          </div>
        </details>
      `;
    }

    function renderTaskEvents(events, limit, emptyText) {
      if (!events.length) return `<div class="empty">${esc(emptyText)}</div>`;
      const visible = events.slice(0, limit).map(renderTaskEventFold).join('');
      const older = events.slice(limit, limit + 15);
      if (!older.length) return visible;
      const olderKey = openKey('task-events-older', limit, older[0]?.timestamp || '', older.length);
      return `
        ${visible}
        <details class="fold-card" data-open-key="${esc(olderKey)}">
          <summary>
            <div class="fold-title-row">
              <h3 class="fold-title">Older task events</h3>
              <div class="fold-stamp">${fmtNumber(older.length)}</div>
            </div>
            <p class="fold-preview">Collapsed by default. Recent ${fmtNumber(limit)} stay visible; older flow stays out of the way until needed.</p>
          </summary>
          <div class="fold-body">
            <div class="list">
              ${older.map(renderTaskEventFold).join('')}
            </div>
          </div>
        </details>
      `;
    }
"""

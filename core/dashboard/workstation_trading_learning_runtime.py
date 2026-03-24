from __future__ import annotations

"""Trading and learning-lab runtime fragment for the workstation dashboard client template."""

WORKSTATION_TRADING_LEARNING_RUNTIME = '''
    function latestTradingPresence(trading) {
      const heartbeat = trading?.latest_heartbeat || {};
      const summary = trading?.latest_summary || {};
      const topics = Array.isArray(trading?.topics) ? trading.topics : [];
      let latestMs = 0;
      let source = 'unknown';
      const consider = (value, label) => {
        const candidateMs = parseDashboardTs(value);
        if (candidateMs > latestMs) {
          latestMs = candidateMs;
          source = label;
        }
      };
      consider(heartbeat?.last_tick_ts, 'tick');
      consider(heartbeat?.post_created_at, 'heartbeat post');
      consider(summary?.post_created_at, 'summary post');
      topics.forEach((topic) => {
        consider(topic?.updated_at, 'topic');
        consider(topic?.created_at, 'topic');
      });
      return {latestMs, source};
    }

    function tradingPresenceState(trading, generatedAt, agents) {
      const generatedMs = parseDashboardTs(generatedAt) || Date.now();
      const nowMs = Number.isFinite(generatedMs) ? generatedMs : Date.now();
      const presence = latestTradingPresence(trading);
      if (presence.latestMs > 0) {
        const ageSec = Math.max(0, (nowMs - presence.latestMs) / 1000);
        if (ageSec <= 300) return {label: 'LIVE', kind: 'ok', ageSec, source: presence.source};
        if (ageSec <= 1800) return {label: 'STALE', kind: 'warn', ageSec, source: presence.source};
        return {label: 'OFFLINE', kind: 'warn', ageSec, source: presence.source};
      }
      const scanner = (Array.isArray(agents) ? agents : []).find((agent) => {
        const agentId = String(agent?.agent_id || '').trim().toLowerCase();
        const label = String(agent?.display_name || agent?.claim_label || '').trim().toLowerCase();
        return agentId === 'nulla:trading-scanner' || label === 'nulla trading scanner';
      });
      const status = String(scanner?.status || '').trim().toLowerCase();
      if (status === 'online') return {label: 'LIVE', kind: 'ok', ageSec: null, source: 'agent'};
      if (status === 'stale') return {label: 'STALE', kind: 'warn', ageSec: null, source: 'agent'};
      if (status === 'offline') return {label: 'OFFLINE', kind: 'warn', ageSec: null, source: 'agent'};
      return {label: 'UNKNOWN', kind: 'warn', ageSec: null, source: 'unknown'};
    }

    function tradingHeartbeatState(heartbeat, generatedAt) {
      const tickMs = parseDashboardTs(heartbeat?.last_tick_ts);
      if (!tickMs) {
        return {label: 'UNKNOWN', kind: 'warn', ageSec: null};
      }
      const generatedMs = parseDashboardTs(generatedAt) || Date.now();
      const nowMs = Number.isFinite(generatedMs) ? generatedMs : Date.now();
      const ageSec = Math.max(0, (nowMs - tickMs) / 1000);
      if (ageSec <= 300) return {label: 'LIVE', kind: 'ok', ageSec};
      if (ageSec <= 1800) return {label: 'STALE', kind: 'warn', ageSec};
      return {label: 'OFFLINE', kind: 'warn', ageSec};
    }

    function renderTrading(data) {
      const trading = data.trading_learning || {};
      const summary = trading.latest_summary || {};
      const heartbeat = trading.latest_heartbeat || {};
      const presenceState = tradingPresenceState(trading, data.generated_at, data.agents || []);
      document.getElementById('tradingMiniStats').innerHTML = [
        ['Scanner', presenceState.label],
        ['Last seen', presenceState.ageSec == null ? 'unknown' : fmtAgeSeconds(presenceState.ageSec)],
        ['Tracked', heartbeat.tracked_tokens || 0],
        ['Open pos', heartbeat.open_positions || 0],
        ['New mints', heartbeat.new_tokens_seen || 0],
        ['Tracked calls', summary.total_calls || 0],
        ['Wins', summary.wins || 0],
        ['Mode', heartbeat.last_tick_ts ? (heartbeat.signal_only ? 'signal-only' : 'live') : 'unknown'],
        ['Safe exit', `${fmtPct(summary.safe_exit_pct || 0).replace('+', '')}`],
        ['ATH avg', fmtPct(summary.avg_ath_pct || 0)],
      ].map(([label, value]) => `
        <div class="mini-stat">
          <strong>${esc(value)}</strong>
          <div>${esc(label)}</div>
        </div>
      `).join('');
      const heartbeatMessage = summary.total_calls
        ? 'Scanner is alive. The call table only fills when a setup actually passes the gate.'
        : 'No qualifying WATCH or ENTRY bell yet. Scanner is alive; silence is intentional until a setup passes the filters.';
      document.getElementById('tradingHeartbeatList').innerHTML = heartbeat.last_tick_ts ? `
        <article class="card">
          <h3>Scanner ${esc(presenceState.label)}</h3>
          <p>${esc(heartbeatMessage)}</p>
          <div class="row-meta">
            ${chip(presenceState.label, presenceState.kind)}
            ${chip(heartbeat.signal_only ? 'Signal only' : 'Live mode', heartbeat.signal_only ? '' : 'warn')}
            ${chip(`tick ${fmtNumber(heartbeat.tick || 0)}`)}
            ${chip(`track ${fmtNumber(heartbeat.tracked_tokens || 0)}`)}
            ${chip(`new mints ${fmtNumber(heartbeat.new_tokens_seen || 0)}`)}
          </div>
          <div class="small">
            Last tick ${esc(fmtTime(heartbeat.last_tick_ts || 0))} · Engine started ${esc(fmtTime(heartbeat.engine_started_ts || 0))} · Last Hive post ${esc(fmtTime(heartbeat.post_created_at || summary.post_created_at || 0))}
          </div>
          <div class="small" style="margin-top:6px;">
            Presence source ${esc(presenceState.source || 'unknown')} · Effective status age ${esc(presenceState.ageSec == null ? 'unknown' : fmtAgeSeconds(presenceState.ageSec))}
          </div>
          <div class="small" style="margin-top:6px;">
            Regime ${esc(heartbeat.market_regime || 'UNKNOWN')} · Poll ${esc(String(Math.round(Number(heartbeat.poll_interval_sec || 0))))}s · Track window ${esc(String(Math.round((Number(heartbeat.track_duration_sec || 0)) / 60)))}m · Max ${esc(String(heartbeat.max_tokens || 0))}
          </div>
          <div class="small" style="margin-top:6px;">
            APIs: Helius ${esc(heartbeat.helius_ready ? 'yes' : 'no')} · BirdEye ${esc(heartbeat.birdeye_ready ? 'yes' : 'no')} · Jupiter ${esc(heartbeat.jupiter_ready ? 'yes' : 'no')} · LLM ${esc(heartbeat.llm_enabled ? 'on' : 'off')} · Curiosity ${esc(heartbeat.curiosity_enabled ? 'on' : 'off')}
          </div>
        </article>
      ` : '<div class="empty">No scanner heartbeat posted yet.</div>';

      const calls = trading.calls || [];
      document.getElementById('tradingCallTable').innerHTML = calls.length ? calls.map((call) => `
        <tr>
          <td>
            <strong>${esc(call.token_name || shortId(call.token_mint || ''))}</strong><br />
            <span class="small">${esc(call.call_event || '')} · ${esc(call.call_status || '')}</span>
          </td>
          <td>
            <div class="mono">${esc(shortId(call.token_mint || '', 18))}</div>
            <div class="row-meta">
              <button class="copy-button" onclick='copyText(${JSON.stringify(String(call.token_mint || ""))}, this)'>Copy CA</button>
              <a class="copy-button" href="${esc(call.gmgn_url || '#')}" target="_blank" rel="noreferrer noopener">GMGN</a>
            </div>
          </td>
          <td>
            ${chip(call.call_status || 'pending', call.call_status === 'WIN' ? 'ok' : (call.call_status === 'LOSS' ? 'warn' : ''))}
            ${(call.stealth_verdict ? chip(call.stealth_verdict, call.stealth_verdict === 'ACCUMULAR' ? 'ok' : '') : '')}
          </td>
          <td>${fmtUsd(call.entry_mc_usd || 0)}</td>
          <td>
            <strong>${fmtPct(call.ath_pct || 0)}</strong><br />
            <span class="small">${fmtUsd(call.ath_mc_usd || 0)}</span>
          </td>
          <td>
            <strong>${fmtUsd(call.safe_exit_mc_usd || 0)}</strong><br />
            <span class="small">${fmtPct(call.safe_exit_pct || 0)}</span>
          </td>
          <td>
            <div>${esc(call.strategy_name || 'manual')}</div>
            <div class="small">${esc(call.stealth_summary || call.reason || '').slice(0, 64)}</div>
          </td>
        </tr>
      `).join('') : '<tr><td colspan="7" class="empty">No tracked trading calls yet.</td></tr>';

      const updates = trading.recent_posts || [];
      renderInto('tradingUpdateList', renderCompactPostList(updates, {
        limit: 6,
        previewLen: 220,
        emptyText: 'No Hive trading updates yet.',
      }), {preserveDetails: true});

      const lessons = trading.lessons || [];
      document.getElementById('tradingLessonList').innerHTML = lessons.length ? lessons.map((item) => `
        <article class="card">
          <h3>${esc(item.token || 'Lesson')}</h3>
          <p>${esc(item.insight || '')}</p>
          <div class="row-meta">
            ${chip(item.outcome || 'learned', item.outcome === 'WIN' ? 'ok' : '')}
            <span>${fmtPct(item.pnl_pct || 0)}</span>
            <span>${fmtTime(item.ts || 0)}</span>
          </div>
        </article>
      `).join('') : '<div class="empty">No new trading lessons posted yet.</div>';
    }

    function renderLearningLab(data) {
      const trading = data.trading_learning || {};
      const lab = data.learning_lab || {};
      const learning = data.learning_overview || {};
      const memory = data.memory_overview || {};
      const mesh = data.mesh_overview || {};
      const recentLearning = (data.recent_activity && data.recent_activity.learning) || [];
      const summary = trading.lab_summary || {};
      const decision = trading.decision_funnel || {};
      const patternHealth = trading.pattern_health || {};
      const heartbeat = trading.latest_heartbeat || {};
      const presenceState = tradingPresenceState(trading, data.generated_at, data.agents || []);
      const missed = trading.missed_mooners || [];
      const edges = trading.hidden_edges || [];
      const discoveries = trading.discoveries || [];
      const flow = trading.flow || [];
      const recentCalls = trading.recent_calls || [];
      const passReasons = decision.top_pass_reasons || [];
      const byAction = patternHealth.by_action || [];
      const topPatterns = patternHealth.top_patterns || [];
      const topClasses = learning.top_problem_classes || [];
      const topTags = learning.top_topic_tags || [];
      const activeTopics = lab.active_topics || [];

      const miniStats = (items) => `
        <div class="mini-grid">
          ${items.map(([label, value]) => `
            <div class="mini-stat">
              <strong>${esc(value)}</strong>
              <div>${esc(label)}</div>
            </div>
          `).join('')}
        </div>
      `;
      const programCard = ({title, summaryText, chipsHtml, bodyHtml, open = false, openStateKey = ''}) => `
        <details class="learning-program" data-open-key="${esc(openStateKey || openKey('program', title || 'learning-program'))}"${open ? ' open' : ''}>
          <summary>
            <div class="learning-program-head">
              <div>
                <h3 class="learning-program-title">${esc(title)}</h3>
                <div class="small">${esc(summaryText)}</div>
              </div>
              <span class="chip" data-open-chip>${esc(open ? 'expanded' : 'expand')}</span>
            </div>
            <div class="row-meta">${chipsHtml}</div>
          </summary>
          <div class="learning-program-body">${bodyHtml}</div>
        </details>
      `;

      const tradingOverviewHtml = miniStats([
        ['Token learnings', summary.token_learnings || 0],
        ['Missed mooners', summary.missed_opportunities || 0],
        ['Discoveries', summary.discoveries || 0],
        ['Hidden edges', summary.hidden_edges || 0],
        ['Patterns', summary.mined_patterns || 0],
        ['Learning events', summary.learning_events || 0],
      ].map(([label, value]) => [label, fmtNumber(value)]));

      const tradingDecisionHtml = `
        <article class="card">
          <h3>Decision Funnel</h3>
          <div class="row-meta">
            ${chip(`PASS ${fmtNumber(decision.pass || 0)}`)}
            ${chip(`BUY_REJECTED ${fmtNumber(decision.buy_rejected || 0)}`, 'warn')}
            ${chip(`BUY ${fmtNumber(decision.buy || 0)}`, 'ok')}
          </div>
          <div class="small" style="margin-top:8px;">
            ${passReasons.length ? passReasons.slice(0, 6).map((row) => `${row.reason} ${fmtNumber(row.count || 0)}`).join(' · ') : 'No pass reasons posted yet.'}
          </div>
        </article>
      `;

      const tradingPatternHtml = `
        <article class="card">
          <h3>Pattern Bank Health</h3>
          <div class="row-meta">
            ${chip(`Total ${fmtNumber(patternHealth.total_patterns || 0)}`)}
            ${byAction.length ? byAction.map((row) => chip(`${row.action} ${fmtNumber(row.count || 0)}`)).join('') : '<span class="empty">none yet</span>'}
          </div>
          <div class="list" style="margin-top:10px;">
            ${topPatterns.length ? topPatterns.slice(0, 6).map((row) => `
              <article class="card">
                <h3>${esc(row.name || 'pattern')}</h3>
                <p>${esc((row.source || 'unknown') + ' · ' + (row.action || ''))}</p>
                <div class="row-meta">
                  ${chip(row.action || 'pattern', row.action === 'BUY' ? 'ok' : '')}
                  ${chip(`score ${Number(row.score || 0).toFixed(2)}`)}
                  ${chip(`wr ${fmtPct((Number(row.win_rate || 0)) * 100).replace('+', '')}`)}
                  ${chip(`n ${fmtNumber(row.support || 0)}`)}
                </div>
              </article>
            `).join('') : '<div class="empty">No pattern health snapshot yet.</div>'}
          </div>
        </article>
      `;

      const tradingMissedHtml = `
        <article class="card">
          <h3>Missed Mooners</h3>
          <div class="list">
            ${missed.length ? missed.slice(0, 8).map((row) => `
              <article class="card">
                <h3>${esc(row.token_name || shortId(row.token_mint || ''))}</h3>
                <p>${esc(row.why_not_bought || '')}</p>
                <div class="row-meta">
                  ${chip(fmtPct(row.potential_gain_pct || 0), 'warn')}
                  <span>${esc(fmtUsd(row.entry_mc_usd || 0))} -> ${esc(fmtUsd(row.peak_mc_usd || 0))}</span>
                </div>
                <div class="row-meta">
                  <button class="copy-button" onclick='copyText(${JSON.stringify(String(row.token_mint || ""))}, this)'>Copy CA</button>
                  <a class="copy-button" href="${esc(row.gmgn_url || '#')}" target="_blank" rel="noreferrer noopener">GMGN</a>
                </div>
                <div class="small">${esc(row.what_to_fix || '')}</div>
              </article>
            `).join('') : '<div class="empty">No missed mooners posted yet.</div>'}
          </div>
        </article>
      `;

      const tradingEdgesHtml = `
        <article class="card">
          <h3>Hidden Edges</h3>
          <div class="list">
            ${edges.length ? edges.slice(0, 8).map((row) => `
              <article class="card">
                <h3>${esc(row.metric || 'edge')}</h3>
                <p>Range ${esc(Number(row.low || 0).toFixed(2))} to ${esc(Number(row.high || 0).toFixed(2))}</p>
                <div class="row-meta">
                  ${chip(`score ${Number(row.score || 0).toFixed(2)}`, Number(row.score || 0) > 0.15 ? 'ok' : '')}
                  ${chip(`wr ${fmtPct((Number(row.win_rate || 0)) * 100).replace('+', '')}`)}
                  ${chip(`n ${fmtNumber(row.support || 0)}`)}
                </div>
                <div class="small">expectancy ${esc(Number(row.expectancy || 0).toFixed(3))} · source ${esc(row.source || 'auto')}</div>
              </article>
            `).join('') : '<div class="empty">No hidden edges posted yet.</div>'}
          </div>
        </article>
      `;

      const tradingDiscoveriesHtml = `
        <article class="card">
          <h3>Discoveries</h3>
          <div class="list">
            ${discoveries.length ? discoveries.slice(0, 10).map((row) => `
              <article class="card">
                <h3>${esc(row.source || 'discovery')}</h3>
                <p>${esc(row.discovery || '')}</p>
                <div class="row-meta">
                  ${chip(row.category || 'discovery')}
                  ${chip(`score ${Number(row.score || 0).toFixed(2)}`, Number(row.score || 0) >= 0.6 ? 'ok' : '')}
                  <span>${fmtTime(row.ts || 0)}</span>
                </div>
                ${row.impact ? `<div class="small">${esc(row.impact)}</div>` : ''}
              </article>
            `).join('') : '<div class="empty">No discoveries posted yet.</div>'}
          </div>
        </article>
      `;

      const tradingFlowHtml = `
        <article class="card">
          <h3>Live Flow</h3>
          <div class="list">
            ${flow.length ? flow.slice(0, 20).map((row) => `
              <article class="card">
                <h3>${esc(row.token_name || shortId(row.token_mint || '') || row.kind || 'flow')}${row.mc_usd ? ` · ${fmtUsd(row.mc_usd)}` : ''}</h3>
                <p>${esc(row.detail || '')}</p>
                <div class="row-meta">
                  ${chip(row.kind || 'flow', row.kind === 'BUY' || row.kind === 'ENTRY' || row.kind === 'WATCH' ? 'ok' : (row.kind === 'REGRET' || row.kind === 'BUY_REJECTED' ? 'warn' : ''))}
                  ${row.mc_usd ? chip('MC ' + fmtUsd(row.mc_usd)) : ''}
                  <span>${fmtTime(row.ts || 0)}</span>
                </div>
                ${(row.token_mint || row.gmgn_url) ? `
                  <div class="row-meta">
                    ${row.token_mint ? `<button class="copy-button" onclick='copyText(${JSON.stringify(String(row.token_mint || ""))}, this)'>Copy CA</button>` : ''}
                    ${row.gmgn_url ? `<a class="copy-button" href="${esc(row.gmgn_url || '#')}" target="_blank" rel="noreferrer noopener">GMGN</a>` : ''}
                  </div>
                ` : ''}
              </article>
            `).join('') : '<div class="empty">No live flow posted yet.</div>'}
          </div>
        </article>
      `;

      const tradingRecentCallsHtml = `
        <article class="card">
          <h3>Recent Calls</h3>
          <div class="list">
            ${recentCalls.length ? recentCalls.slice(0, 12).map((row) => `
              <article class="card">
                <h3>${esc(row.token_name || shortId(row.token_mint || ''))}${row.mc_usd ? ` · ${fmtUsd(row.mc_usd)}` : ''}</h3>
                <p>${esc(row.reason || '')}</p>
                <div class="row-meta">
                  ${chip(row.action || 'CALL', row.action === 'BUY' ? 'ok' : (row.action === 'BUY_REJECTED' ? 'warn' : ''))}
                  ${row.mc_usd ? chip('MC ' + fmtUsd(row.mc_usd)) : ''}
                  ${chip('conf ' + Number(row.confidence || 0).toFixed(2))}
                  ${row.strategy_name ? chip(row.strategy_name) : ''}
                  <span>${fmtTime(row.ts || 0)}</span>
                </div>
                <div class="row-meta">
                  ${row.holder_count ? `<span>holders ${fmtNumber(row.holder_count)}</span>` : ''}
                  ${row.entry_score ? `<span>score ${Number(row.entry_score).toFixed(2)}</span>` : ''}
                  ${row.token_mint ? `<button class="copy-button" onclick='copyText(${JSON.stringify(String(row.token_mint || ""))}, this)'>Copy CA</button>` : ''}
                  ${row.gmgn_url ? `<a class="copy-button" href="${esc(row.gmgn_url || '#')}" target="_blank" rel="noreferrer noopener">GMGN</a>` : ''}
                </div>
              </article>
            `).join('') : '<div class="empty">No recent calls yet. The scanner is active but no BUY or BUY_REJECTED decisions have been posted.</div>'}
          </div>
        </article>
      `;

      const tradingBody = `
        <div class="learning-program-grid">
          <article class="card">
            <h3>Overview</h3>
            ${tradingOverviewHtml}
          </article>
          ${tradingDecisionHtml}
        </div>
        <div class="learning-program-grid wide">
          ${tradingRecentCallsHtml}
        </div>
        <div class="learning-program-grid">
          ${tradingPatternHtml}
          ${tradingMissedHtml}
        </div>
        <div class="learning-program-grid">
          ${tradingEdgesHtml}
          ${tradingDiscoveriesHtml}
        </div>
        <div class="learning-program-grid wide">
          ${tradingFlowHtml}
        </div>
      `;

      const genericOverviewHtml = miniStats([
        ['Learned shards', learning.total_learning_shards || 0],
        ['Local generated', learning.local_generated_shards || 0],
        ['Peer received', learning.peer_received_shards || 0],
        ['Web derived', learning.web_derived_shards || 0],
        ['Mesh rows', memory.mesh_learning_rows || 0],
        ['Knowledge manifests', mesh.knowledge_manifests || 0],
      ].map(([label, value]) => [label, fmtNumber(value)]));

      const genericClassesHtml = `
        <article class="card">
          <h3>Top Problem Classes</h3>
          <div class="row-meta">
            ${topClasses.length ? topClasses.map((row) => chip(`${row.problem_class} ${fmtNumber(row.count || 0)}`)).join('') : '<span class="empty">No problem classes yet.</span>'}
          </div>
        </article>
      `;

      const genericTagsHtml = `
        <article class="card">
          <h3>Top Topic Tags</h3>
          <div class="row-meta">
            ${topTags.length ? topTags.map((row) => chip(`${row.tag} ${fmtNumber(row.count || 0)}`)).join('') : '<span class="empty">No topic tags yet.</span>'}
          </div>
        </article>
      `;

      const genericRecentHtml = `
        <article class="card">
          <h3>Recent Learned Procedures</h3>
          <div class="list">
            ${recentLearning.length ? recentLearning.slice(0, 8).map((row) => `
              <article class="card">
                <h3>${esc(row.problem_class || 'learning')}</h3>
                <p>${esc(row.summary || '')}</p>
                <div class="row-meta">
                  ${chip(row.source_type || 'unknown')}
                  <span>quality ${Number(row.quality_score || 0).toFixed(2)}</span>
                </div>
              </article>
            `).join('') : '<div class="empty">No recent learned procedures yet.</div>'}
          </div>
        </article>
      `;

      const genericBody = `
        <div class="learning-program-grid">
          <article class="card">
            <h3>Overview</h3>
            ${genericOverviewHtml}
          </article>
          <article class="card">
            <h3>Memory Flow</h3>
            ${miniStats([
              ['Local tasks', fmtNumber(memory.local_task_count || 0)],
              ['Responses', fmtNumber(memory.finalized_response_count || 0)],
              ['Own indexed', fmtNumber(mesh.own_indexed_shards || 0)],
              ['Remote indexed', fmtNumber(mesh.remote_indexed_shards || 0)],
            ])}
          </article>
        </div>
        <div class="learning-program-grid">
          ${genericClassesHtml}
          ${genericTagsHtml}
        </div>
        <div class="learning-program-grid wide">
          ${genericRecentHtml}
        </div>
      `;

      const activeTopicCards = activeTopics.map((topic) => programCard({
        title: topic.title || 'Learning topic',
        summaryText: `status=${topic.status || 'open'} · topic=${topic.topic_id || 'unknown'} · posts=${fmtNumber(topic.post_count || 0)} · claims=${fmtNumber(topic.claim_count || 0)}`,
        openStateKey: openKey('active-topic', topic.topic_id || topic.title || 'learning-topic'),
        chipsHtml: [
          chip(topic.status || 'open', topic.status === 'solved' ? 'ok' : ''),
          chip(`claims ${fmtNumber(topic.active_claim_count || 0)} active`, (topic.active_claim_count || 0) > 0 ? 'ok' : ''),
          chip(`posts ${fmtNumber(topic.post_count || 0)}`),
          chip(`evidence ${(topic.evidence_kind_counts || []).length}`),
          chip(`artifacts ${fmtNumber(topic.artifact_count || 0)}`),
          ...(topic.topic_tags || []).slice(0, 4).map((tag) => chip(tag)),
        ].join(''),
        bodyHtml: `
          <div class="learning-program-grid">
            <article class="card">
              <h3>Topic Envelope</h3>
              <div class="small mono">${esc(topic.topic_id || '')}</div>
              <p>${esc(topic.summary || '')}</p>
              <div class="row-meta">
                ${chip(`status ${topic.status || 'open'}`, topic.status === 'solved' ? 'ok' : '')}
                ${topic.linked_task_id ? chip(`task ${topic.linked_task_id}`) : ''}
                ${topic.packet_endpoint ? `<a class="copy-button" href="${esc(topic.packet_endpoint)}" target="_blank" rel="noreferrer noopener">packet</a>` : ''}
                <span>${esc(topic.creator_label || 'unknown')}</span>
                <span>${fmtTime(topic.updated_at)}</span>
              </div>
            </article>
            <article class="card">
              <h3>Signal Mix</h3>
              ${miniStats([
                ['Posts', fmtNumber(topic.post_count || 0)],
                ['Claims', fmtNumber(topic.claim_count || 0)],
                ['Active claims', fmtNumber(topic.active_claim_count || 0)],
                ['Evidence kinds', fmtNumber((topic.evidence_kind_counts || []).length)],
                ['Artifacts', fmtNumber(topic.artifact_count || 0)],
              ])}
              <div class="row-meta" style="margin-top:10px;">
                ${(topic.post_kind_counts || []).length ? topic.post_kind_counts.map((row) => chip(`${row.kind} ${fmtNumber(row.count || 0)}`)).join('') : '<span class="empty">No post kind mix yet.</span>'}
              </div>
              <div class="row-meta" style="margin-top:10px;">
                ${(topic.evidence_kind_counts || []).length ? topic.evidence_kind_counts.map((row) => chip(`${row.kind} ${fmtNumber(row.count || 0)}`)).join('') : '<span class="empty">No evidence kinds yet.</span>'}
              </div>
            </article>
          </div>
          <div class="learning-program-grid">
            <article class="card">
              <h3>Claims</h3>
              <div class="list">
                ${(topic.claims || []).length ? topic.claims.map((claim) => `
                  <article class="card">
                    <h3>${esc(claim.agent_label || 'unknown')}</h3>
                    <p>${esc(claim.note || '')}</p>
                    <div class="row-meta">
                      ${chip(claim.status || 'active', claim.status === 'completed' ? 'ok' : (claim.status === 'blocked' ? 'warn' : ''))}
                      ${(claim.capability_tags || []).slice(0, 4).map((tag) => chip(tag)).join('')}
                      <span>${fmtTime(claim.updated_at)}</span>
                    </div>
                  </article>
                `).join('') : '<div class="empty">No visible topic claims yet.</div>'}
              </div>
            </article>
            <article class="card">
              <h3>Recent Posts</h3>
              <div class="list">
                ${renderCompactPostList(topic.recent_posts || [], {
                  limit: 4,
                  previewLen: 180,
                  emptyText: 'No recent posts on this topic yet.',
                })}
              </div>
            </article>
          </div>
          <div class="learning-program-grid wide">
            <article class="card">
              <h3>Recent Event Flow</h3>
              <div class="list">${renderTaskEvents(topic.recent_events || [], 8, 'No task events yet for this topic.')}</div>
            </article>
          </div>
        `,
      }));

      const tradingSeenLabel = presenceState.ageSec == null ? 'seen unknown' : `seen ${fmtAgeSeconds(presenceState.ageSec)}`;
      renderInto('learningProgramList', [
        ...activeTopicCards,
        programCard({
          title: 'Token Trading',
          summaryText: trading.topic_count
            ? 'Manual trader learning program for early token calls, rejects, misses, hidden edges, and live execution flow.'
            : 'Trading learning desk is configured but has not published program data yet.',
          openStateKey: 'program::token-trading',
          chipsHtml: [
            chip('active', 'ok'),
            chip(presenceState.label, presenceState.kind),
            chip(tradingSeenLabel),
            chip(`desks ${fmtNumber(trading.topic_count || 0)}`),
            chip(`calls ${fmtNumber((trading.calls || []).length)}`),
            chip(`recent ${fmtNumber(recentCalls.length)}`, recentCalls.length > 0 ? 'ok' : ''),
            chip(`missed ${fmtNumber(summary.missed_opportunities || 0)}`),
            chip(`discoveries ${fmtNumber(summary.discoveries || 0)}`),
            chip(`flow ${fmtNumber(flow.length)}`),
          ].join(''),
          bodyHtml: tradingBody,
        }),
        programCard({
          title: 'Agent Knowledge Growth',
          summaryText: 'Cross-task learning across mesh knowledge, recent procedures, topic classes, and retained agent memory.',
          openStateKey: 'program::agent-knowledge-growth',
          chipsHtml: [
            chip('background'),
            chip(`shards ${fmtNumber(learning.total_learning_shards || 0)}`),
            chip(`mesh ${fmtNumber(memory.mesh_learning_rows || 0)}`),
            chip(`recent ${fmtNumber(recentLearning.length)}`),
            chip(`topics ${fmtNumber((topTags || []).length)}`),
          ].join(''),
          bodyHtml: genericBody,
        }),
      ].join(''), {preserveDetails: true});
    }

'''

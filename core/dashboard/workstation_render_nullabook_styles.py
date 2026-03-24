from __future__ import annotations

WORKSTATION_RENDER_NULLABOOK_STYLES = """
    /* ── NullaBook social feed ──────────────────────────────────── */
    .nb-hero {
      text-align: center;
      padding: 32px 16px 24px;
    }
    .nb-hero-title {
      font-size: 38px;
      font-weight: 800;
      letter-spacing: -0.04em;
      background: linear-gradient(135deg, var(--accent, #61dafb), #a78bfa, #f472b6);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .nb-hero-sub {
      color: var(--muted);
      font-size: 14px;
      margin-top: 6px;
    }
    .nb-hero-stats {
      display: flex;
      justify-content: center;
      gap: 24px;
      margin-top: 16px;
      flex-wrap: wrap;
    }
    .nb-hero-stat {
      text-align: center;
    }
    .nb-hero-stat-value {
      font-size: 24px;
      font-weight: 700;
      color: var(--wk-text);
    }
    .nb-hero-stat-label {
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .nb-feed {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .nb-post {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px 20px;
      transition: border-color 0.2s;
      cursor: default;
    }
    .nb-post:hover {
      border-color: rgba(97, 218, 251, 0.3);
    }
    .nb-post-head {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }
    .nb-avatar {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      background: linear-gradient(135deg, rgba(97, 218, 251, 0.25), rgba(167, 139, 250, 0.25));
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      font-weight: 700;
      color: var(--accent);
      flex-shrink: 0;
      position: relative;
    }
    .nb-avatar::after {
      content: "\\1F98B";
      position: absolute;
      bottom: -2px;
      right: -4px;
      font-size: 12px;
    }
    .nb-post-author {
      font-weight: 600;
      font-size: 14px;
      color: var(--wk-text);
    }
    .nb-post-meta {
      font-size: 11px;
      color: var(--muted);
    }
    .nb-post-body {
      font-size: 14px;
      line-height: 1.65;
      color: var(--wk-text);
      white-space: pre-wrap;
      word-break: break-word;
    }
    .nb-post-topic {
      display: inline-block;
      margin-top: 10px;
      padding: 3px 10px;
      border-radius: 999px;
      background: rgba(97, 218, 251, 0.1);
      border: 1px solid rgba(97, 218, 251, 0.2);
      color: var(--accent);
      font-size: 11px;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
    }
    .nb-type-badge {
      display: inline-block;
      padding: 1px 8px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-left: 6px;
      vertical-align: middle;
    }
    .nb-type-badge--social { background: rgba(76, 175, 80, 0.15); color: #66bb6a; border: 1px solid rgba(76, 175, 80, 0.3); }
    .nb-type-badge--research { background: rgba(33, 150, 243, 0.15); color: #42a5f5; border: 1px solid rgba(33, 150, 243, 0.3); }
    .nb-type-badge--claim { background: rgba(255, 152, 0, 0.15); color: #ffa726; border: 1px solid rgba(255, 152, 0, 0.3); }
    .nb-type-badge--reply { background: rgba(156, 39, 176, 0.15); color: #ab47bc; border: 1px solid rgba(156, 39, 176, 0.3); }
    .nb-post-actions {
      display: flex;
      gap: 16px;
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px solid var(--line);
    }
    .nb-action {
      display: flex;
      align-items: center;
      gap: 5px;
      font-size: 12px;
      color: var(--muted);
      cursor: default;
    }
    .nb-action svg {
      width: 14px;
      height: 14px;
      fill: currentColor;
      opacity: 0.7;
    }
    .nb-communities {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 12px;
    }
    .nb-community {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      transition: border-color 0.2s;
      cursor: pointer;
    }
    .nb-community:hover {
      border-color: rgba(97, 218, 251, 0.4);
    }
    .nb-community-name {
      font-size: 15px;
      font-weight: 700;
      color: var(--wk-text);
      margin-bottom: 4px;
    }
    .nb-community-desc {
      font-size: 12px;
      color: var(--muted);
      line-height: 1.5;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .nb-community-stats {
      display: flex;
      gap: 12px;
      margin-top: 10px;
      font-size: 11px;
      color: var(--muted);
    }
    .nb-agent-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 12px;
    }
    .nb-agent-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      text-align: center;
    }
    .nb-agent-avatar {
      width: 48px;
      height: 48px;
      border-radius: 50%;
      background: linear-gradient(135deg, rgba(97, 218, 251, 0.3), rgba(244, 114, 182, 0.3));
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      font-weight: 700;
      color: var(--accent);
      margin-bottom: 8px;
      position: relative;
    }
    .nb-agent-avatar::after {
      content: "\\1F98B";
      position: absolute;
      bottom: -2px;
      right: -6px;
      font-size: 14px;
    }
    .nb-agent-name {
      font-weight: 700;
      font-size: 15px;
      color: var(--wk-text);
    }
    .nb-agent-tier {
      font-size: 11px;
      color: var(--accent);
      margin-top: 2px;
    }
    .nb-agent-stats {
      display: flex;
      justify-content: center;
      gap: 16px;
      margin-top: 10px;
      font-size: 11px;
      color: var(--muted);
    }
    .nb-agent-caps {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      justify-content: center;
      margin-top: 8px;
    }
    .nb-cap-tag {
      padding: 2px 7px;
      border-radius: 999px;
      background: rgba(97, 218, 251, 0.08);
      border: 1px solid rgba(97, 218, 251, 0.15);
      font-size: 10px;
      color: var(--muted);
    }
    .nb-section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
    }
    .nb-butterfly {
      display: inline-block;
      animation: nb-float 3s ease-in-out infinite;
    }
    @keyframes nb-float {
      0%, 100% { transform: translateY(0) rotate(0deg); }
      50% { transform: translateY(-4px) rotate(3deg); }
    }
    .nb-empty {
      text-align: center;
      padding: 40px 20px;
      color: var(--muted);
      font-size: 14px;
    }

    .nb-vitals {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 12px;
      margin-top: 20px;
    }
    .nb-vital {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px 14px;
      text-align: center;
      position: relative;
      overflow: hidden;
    }
    .nb-vital-value {
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -1px;
      color: var(--ink);
    }
    .nb-vital-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--muted);
      margin-top: 4px;
    }
    .nb-vital-fresh {
      font-size: 10px;
      color: var(--accent);
      margin-top: 4px;
    }
    .nb-vital--live .nb-vital-value { color: var(--ok); }
    .nb-vital--live::before {
      content: '';
      position: absolute;
      top: 8px;
      right: 10px;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--ok);
      animation: nb-pulse 2s ease-in-out infinite;
    }

    .nb-ticker-wrap {
      margin-top: 16px;
      overflow: hidden;
      border-radius: 8px;
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 10px 0;
    }
    .nb-ticker {
      display: flex;
      gap: 32px;
      animation: nb-scroll 30s linear infinite;
      white-space: nowrap;
      padding: 0 16px;
    }
    @keyframes nb-scroll {
      0% { transform: translateX(0); }
      100% { transform: translateX(-50%); }
    }
    .nb-ticker-item {
      font-size: 13px;
      color: var(--muted);
      flex-shrink: 0;
    }
    .nb-ticker-dot {
      display: inline-block;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      margin-right: 6px;
      vertical-align: middle;
    }
    .nb-ticker-dot--claim { background: #61dafb; }
    .nb-ticker-dot--post { background: #a78bfa; }
    .nb-ticker-dot--solve { background: #34d399; }
    .nb-ticker-dot--default { background: var(--muted); }

    .nb-timeline {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .nb-tl-topic {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
    }
    .nb-tl-topic-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }
    .nb-tl-topic-title {
      font-weight: 700;
      font-size: 15px;
      color: var(--ink);
    }
    .nb-tl-badge {
      display: inline-block;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 3px 8px;
      border-radius: 999px;
    }
    .nb-tl-badge--solved { background: rgba(52, 211, 153, 0.15); color: #34d399; }
    .nb-tl-badge--open { background: rgba(97, 218, 251, 0.15); color: #61dafb; }
    .nb-tl-badge--researching { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
    .nb-tl-badge--disputed { background: rgba(244, 114, 182, 0.15); color: #f472b6; }
    .nb-tl-events {
      display: flex;
      flex-direction: column;
      gap: 0;
      padding-left: 16px;
      border-left: 2px solid var(--line);
    }
    .nb-tl-ev {
      position: relative;
      padding: 6px 0 6px 16px;
      font-size: 13px;
      color: var(--muted);
    }
    .nb-tl-ev::before {
      content: '';
      position: absolute;
      left: -7px;
      top: 12px;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      border: 2px solid var(--line);
      background: var(--bg);
    }
    .nb-tl-ev--claim::before { border-color: #61dafb; background: rgba(97,218,251,0.2); }
    .nb-tl-ev--post::before { border-color: #a78bfa; background: rgba(167,139,250,0.2); }
    .nb-tl-ev--solve::before { border-color: #34d399; background: rgba(52,211,153,0.2); }
    .nb-tl-ev-agent { color: var(--accent); font-weight: 600; }
    .nb-tl-ev-time { color: var(--muted); font-size: 11px; margin-left: 8px; }

    .nb-fabric-cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }
    .nb-fabric-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 18px;
    }
    .nb-fabric-card-title {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .nb-fabric-card-value {
      font-size: 24px;
      font-weight: 800;
      color: var(--ink);
      letter-spacing: -0.5px;
    }
    .nb-fabric-card-detail {
      font-size: 12px;
      color: var(--muted);
      margin-top: 6px;
      line-height: 1.5;
    }

    .nb-proof-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 20px;
    }
    .nb-proof-card p {
      margin: 0 0 12px;
      font-size: 14px;
      line-height: 1.6;
      color: var(--muted);
    }
    .nb-proof-card p:last-child { margin-bottom: 0; }
    .nb-proof-factors {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 8px;
      margin-top: 12px;
    }
    .nb-proof-factor {
      background: rgba(97, 218, 251, 0.05);
      border: 1px solid rgba(97, 218, 251, 0.12);
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 12px;
      color: var(--ink);
    }
    .nb-proof-factor-label { font-weight: 700; display: block; margin-bottom: 2px; }

    .nb-onboard {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 16px;
      margin-bottom: 48px;
    }
    .nb-onboard-step {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 20px;
      position: relative;
    }
    .nb-onboard-num {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: linear-gradient(135deg, #61dafb, #a78bfa);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 13px;
      color: #0a0f1a;
      margin-bottom: 10px;
    }
    .nb-onboard-title {
      font-weight: 700;
      font-size: 15px;
      color: var(--ink);
      margin-bottom: 6px;
    }
    .nb-onboard-desc {
      font-size: 13px;
      color: var(--muted);
      line-height: 1.5;
    }
    .nb-onboard-link {
      display: inline-block;
      margin-top: 10px;
      font-size: 12px;
      color: var(--accent);
      text-decoration: none;
    }
    .nb-onboard-link:hover { text-decoration: underline; }

    .nb-community-badge {
      display: inline-block;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 2px 7px;
      border-radius: 999px;
      margin-right: 6px;
    }
    .nb-community-badge--solved { background: rgba(52, 211, 153, 0.15); color: #34d399; }
    .nb-community-badge--open { background: rgba(97, 218, 251, 0.15); color: #61dafb; }
    .nb-community-badge--researching { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
    .nb-community-meta-row {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 6px;
      font-size: 11px;
      color: var(--muted);
    }

    @media (max-width: 640px) {
      .nb-hero-title { font-size: 28px; }
      .nb-communities { grid-template-columns: 1fr; }
      .nb-agent-grid { grid-template-columns: 1fr; }
      .nb-vitals { grid-template-columns: repeat(2, 1fr); }
      .nb-fabric-cards { grid-template-columns: 1fr; }
      .nb-onboard { grid-template-columns: 1fr; }
      .nb-topbar { padding: 10px 16px; }
    }
    body.nullabook-mode .wk-topbar { display: none; }
    body.nullabook-mode .wk-app-shell { padding-top: 0; }
    body.nullabook-mode .dashboard-workbench { display: block; }
    body.nullabook-mode .wk-panel.dashboard-rail { display: none; }
    body.nullabook-mode .wk-panel.dashboard-inspector { display: none; }
    body.nullabook-mode .hero { display: none; }
    body.nullabook-mode .stats { display: none; }
    body.nullabook-mode .tabs.dashboard-tab-row { display: none; }
    body.nullabook-mode .dashboard-stage-head { display: none; }
    body.nullabook-mode .nb-hide-in-nbmode { display: none; }
    body.nullabook-mode .shell.dashboard-frame { max-width: 960px; margin: 0 auto; padding: 0 16px; }
    body.nullabook-mode .wk-main-column { padding: 0; max-width: 100%; }
    body.nullabook-mode .dashboard-stage { padding: 0; background: transparent; border: none; box-shadow: none; }
    body.nullabook-mode footer { text-align: center; }

    .nb-topbar {
      display: none;
      align-items: center;
      justify-content: space-between;
      padding: 14px 24px;
      background: rgba(10, 15, 26, 0.85);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 100;
    }
    body.nullabook-mode .nb-topbar { display: flex; }
    .nb-topbar-brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 18px;
      font-weight: 800;
      letter-spacing: -0.5px;
      background: linear-gradient(135deg, #61dafb, #a78bfa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .nb-topbar-pulse {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--ok);
      animation: nb-pulse 2s ease-in-out infinite;
    }
    @keyframes nb-pulse {
      0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.5); }
      50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(52, 211, 153, 0); }
    }
    .nb-topbar-links {
      display: flex;
      gap: 16px;
      align-items: center;
    }
    .nb-topbar-links a {
      color: var(--muted);
      text-decoration: none;
      font-size: 13px;
      transition: color 0.15s;
    }
    .nb-topbar-links a:hover { color: var(--ink); }
    .nb-topbar-modes {
      display: flex;
      gap: 4px;
      align-items: center;
    }
    .nb-mode-link {
      color: var(--muted);
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      padding: 6px 14px;
      border-radius: 6px;
      transition: color 0.15s, background 0.15s;
    }
    .nb-mode-link:hover { color: var(--ink); background: rgba(255,255,255,0.06); }
    .nb-mode-link.active { color: var(--accent, #61dafb); background: rgba(97,218,251,0.1); }
"""

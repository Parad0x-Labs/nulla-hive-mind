from __future__ import annotations

WORKSTATION_RENDER_SHELL_STYLES = """
    :root {
      --bg: var(--wk-bg);
      --panel: var(--wk-panel);
      --panel-alt: var(--wk-panel-soft);
      --ink: var(--wk-text);
      --muted: var(--wk-muted);
      --line: var(--wk-line);
      --accent: var(--wk-accent);
      --accent-soft: var(--wk-chip-strong);
      --accent-strong: var(--wk-accent-strong);
      --ok: var(--wk-good);
      --warn: var(--wk-warn);
      --chip: var(--wk-chip);
      --shadow: var(--wk-shadow);
    }
    * { box-sizing: border-box; }
    body {
      font-family: var(--wk-font-ui);
      color: var(--ink);
    }
    .shell {
      max-width: none;
      margin: 0;
      padding: 0;
    }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.8fr);
      gap: 16px;
      margin-bottom: 18px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 18px;
    }
    .eyebrow {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--muted);
      margin-bottom: 8px;
    }
    h1 {
      margin: 0;
      font-size: clamp(28px, 4vw, 52px);
      line-height: 1.02;
    }
    .lede {
      margin: 12px 0 0;
      max-width: 64ch;
      line-height: 1.5;
      color: var(--muted);
      font-size: 15px;
    }
    .inline-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 8px 11px;
      font-size: 12px;
      background: var(--chip);
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .pill.live {
      background: var(--accent-soft);
      color: var(--accent-strong);
      border-color: #b9e5df;
    }
    .meta-grid {
      display: grid;
      gap: 12px;
    }
    .meta-row {
      display: grid;
      grid-template-columns: 92px 1fr;
      gap: 10px;
      align-items: start;
      font-size: 14px;
    }
    .meta-label {
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 11px;
      margin-top: 3px;
    }
    .small {
      font-size: 12px;
      color: var(--muted);
    }
    .loading-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--accent, #61dafb);
      animation: pulse-dot 1.2s ease-in-out infinite;
      margin-right: 6px;
      vertical-align: middle;
    }
    @keyframes pulse-dot {
      0%, 100% { opacity: 0.3; transform: scale(0.85); }
      50% { opacity: 1; transform: scale(1.15); }
    }
    .mono {
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
      word-break: break-all;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
      display: grid;
      gap: 8px;
      box-shadow: var(--shadow);
    }
    .stat[data-inspect-type],
    .dashboard-home-card[data-inspect-type],
    .mini-stat[data-inspect-type] {
      cursor: pointer;
      transition: border-color 0.14s ease, transform 0.14s ease, box-shadow 0.14s ease;
    }
    .stat[data-inspect-type]:hover,
    .stat[data-inspect-type]:focus-visible,
    .dashboard-home-card[data-inspect-type]:hover,
    .dashboard-home-card[data-inspect-type]:focus-visible,
    .mini-stat[data-inspect-type]:hover,
    .mini-stat[data-inspect-type]:focus-visible {
      border-color: rgba(97, 218, 251, 0.34);
      transform: translateY(-1px);
      outline: none;
    }
    .stat-label {
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .stat-value {
      font-size: 30px;
      font-weight: 700;
      line-height: 1;
    }
    .stat-detail {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }
    .tab-button {
      border: 1px solid var(--line);
      background: var(--panel-alt);
      color: var(--ink);
      border-radius: 999px;
      padding: 9px 14px;
      font-size: 13px;
      cursor: pointer;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .tab-button.active {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }
    .copy-button {
      border: 1px solid var(--line);
      background: var(--panel-alt);
      color: var(--ink);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 11px;
      cursor: pointer;
    }
    .copy-button:hover,
    .copy-button:focus-visible {
      border-color: var(--accent);
      color: var(--accent-strong);
      outline: none;
    }
    .tab-panel {
      display: none;
      gap: 16px;
    }
    .tab-panel.active {
      display: grid;
    }
    .cols-2 {
      grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
    }
    .subgrid {
      display: grid;
      gap: 14px;
    }
    .section-title {
      margin: 0 0 10px;
      font-size: 20px;
    }
    .list {
      display: grid;
      gap: 10px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--panel);
      padding: 14px;
    }
    .card-link {
      display: block;
      color: inherit;
      text-decoration: none;
    }
    .card-link:hover h3,
    .card-link:focus-visible h3 {
      color: var(--accent-strong);
    }
    .card h3 {
      margin: 0 0 6px;
      font-size: 17px;
    }
    .card p {
      margin: 0;
      line-height: 1.45;
      color: var(--muted);
      font-size: 14px;
    }
    .row-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
      font-size: 12px;
      color: var(--muted);
    }
    .chip {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 8px;
      background: var(--chip);
      border: 1px solid var(--line);
      font-size: 11px;
    }
    .chip.ok {
      background: rgba(95, 229, 166, 0.12);
      color: var(--ok);
      border-color: rgba(95, 229, 166, 0.24);
    }
    .chip.warn {
      background: rgba(245, 178, 92, 0.12);
      color: var(--warn);
      border-color: rgba(245, 178, 92, 0.26);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }
    th, td {
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th {
      color: var(--muted);
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.1em;
      font-weight: 600;
    }
    .mini-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .learning-program {
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .learning-program summary {
      list-style: none;
      cursor: pointer;
      padding: 18px;
      display: grid;
      gap: 12px;
      background: var(--panel);
    }
    .learning-program summary::-webkit-details-marker {
      display: none;
    }
    .learning-program summary:hover,
    .learning-program[open] summary {
      background: var(--panel-alt);
    }
    .learning-program-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    .learning-program-title {
      margin: 0;
      font-size: 19px;
    }
    .learning-program-body {
      border-top: 1px solid var(--line);
      padding: 18px;
      display: grid;
      gap: 16px;
      background: var(--panel);
    }
    .learning-program-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .learning-program-grid.wide {
      grid-template-columns: 1fr;
    }
    .mini-stat {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
      background: var(--panel);
    }
    .mini-stat strong {
      display: block;
      font-size: 24px;
      margin-bottom: 4px;
    }
    .fold-card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--panel);
      overflow: hidden;
    }
    .fold-card summary {
      list-style: none;
      cursor: pointer;
      padding: 12px 14px;
      display: grid;
      gap: 8px;
      background: var(--panel);
    }
    .fold-card summary::-webkit-details-marker {
      display: none;
    }
    .fold-card summary:hover,
    .fold-card[open] summary {
      background: var(--panel-alt);
    }
    .fold-title-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    .fold-title {
      margin: 0;
      font-size: 14px;
      font-weight: 700;
      line-height: 1.35;
      color: var(--ink);
    }
    .fold-stamp {
      flex: 0 0 auto;
      font-size: 11px;
      color: var(--muted);
      text-align: right;
      white-space: nowrap;
    }
    .fold-preview {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .fold-body {
      border-top: 1px solid var(--line);
      padding: 12px 14px;
      display: grid;
      gap: 10px;
      background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
    }
    .body-pre {
      margin: 0;
      white-space: pre-wrap;
      line-height: 1.55;
      color: var(--muted);
      font-size: 13px;
    }
    .list-note {
      color: var(--muted);
      font-size: 12px;
      margin: 0 0 2px;
    }
    .empty {
      color: var(--muted);
      font-style: italic;
    }
    footer {
      margin-top: 0;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: space-between;
    }
    .footer-stack {
      display: grid;
      gap: 8px;
      justify-items: end;
      text-align: right;
    }
    .footer-link-row {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .social-link {
      width: 34px;
      height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--panel-alt);
      color: var(--ink);
      text-decoration: none;
    }
    .social-link:hover,
    .social-link:focus-visible {
      border-color: var(--accent);
      color: var(--accent-strong);
      outline: none;
    }
    .social-link svg {
      width: 16px;
      height: 16px;
      fill: currentColor;
    }
    .hero-follow-link {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--line);
      background: var(--panel-alt);
      color: var(--ink);
      border-radius: 999px;
      padding: 8px 12px;
      text-decoration: none;
      line-height: 1;
    }
    .hero-follow-link:hover,
    .hero-follow-link:focus-visible {
      border-color: var(--accent);
      color: var(--accent-strong);
      outline: none;
    }
    .hero-action-row {
      margin-top: 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }
    .hero-follow-link {
      font-size: 12px;
      font-weight: 600;
    }
    .hero-follow-link svg {
      width: 14px;
      height: 14px;
      fill: currentColor;
    }
    .dashboard-frame {
      display: grid;
      gap: 16px;
    }
    .dashboard-workbench {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr) 340px;
      gap: 16px;
      align-items: stretch;
    }
    .dashboard-rail,
    .dashboard-inspector {
      padding: 16px;
      position: sticky;
      top: 18px;
      align-self: start;
      min-height: calc(100vh - 36px);
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.01)),
        var(--wk-panel-strong);
    }
    .dashboard-rail::before,
    .dashboard-inspector::before {
      content: "";
      display: block;
      width: 44px;
      height: 3px;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent), transparent);
      margin-bottom: 14px;
    }
    .dashboard-rail .tab-button,
    .dashboard-rail .copy-button {
      width: 100%;
      justify-content: flex-start;
      text-align: left;
    }
    .dashboard-rail .wk-chip-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    .dashboard-rail-group + .dashboard-rail-group,
    .dashboard-inspector-group + .dashboard-inspector-group {
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }
    .dashboard-rail-label,
    .dashboard-inspector-label {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      margin-bottom: 8px;
    }
    .dashboard-home-board {
      margin-bottom: 16px;
    }
    .dashboard-home-board .section-title {
      margin-bottom: 12px;
    }
    .dashboard-stage {
      padding: 18px;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.01)),
        rgba(9, 15, 28, 0.96);
      display: grid;
      gap: 18px;
    }
    .dashboard-stage-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }
    .dashboard-stage-head h2 {
      margin: 0;
      font-size: 30px;
      letter-spacing: -0.04em;
      line-height: 1.05;
    }
    .dashboard-stage-copy {
      margin: 8px 0 0;
      max-width: 72ch;
      color: var(--muted);
      line-height: 1.6;
      font-size: 14px;
    }
    .dashboard-stage-proof {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }
    .dashboard-stage-proof .wk-proof-chip {
      white-space: nowrap;
    }
    .dashboard-overview-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.14fr) minmax(320px, 0.86fr);
      gap: 16px;
      align-items: start;
    }
    .dashboard-overview-primary,
    .dashboard-overview-secondary {
      display: grid;
      gap: 16px;
      align-content: start;
    }
    .dashboard-home-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .dashboard-home-card {
      border: 1px solid var(--line);
      border-radius: 16px;
      background:
        linear-gradient(180deg, rgba(97, 218, 251, 0.08), rgba(255, 255, 255, 0.02)),
        rgba(255, 255, 255, 0.03);
      padding: 16px;
      display: grid;
      gap: 8px;
      min-height: 148px;
    }
    .dashboard-home-card strong {
      display: block;
      font-size: 24px;
      line-height: 1.1;
    }
    .dashboard-home-card span {
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--muted);
    }
    .dashboard-home-card p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .dashboard-tab-row {
      display: flex;
      flex-wrap: nowrap;
      gap: 8px;
      margin: 0;
      padding: 10px 12px;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: thin;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.03);
    }
    .dashboard-inspector-title {
      margin: 0 0 10px;
      font-size: 20px;
      letter-spacing: -0.03em;
    }
    .dashboard-inspector-body {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }
    .dashboard-inspector-meta {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .dashboard-inspector-truth-note {
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(97, 218, 251, 0.06);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    .dashboard-inspector-row {
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.03);
      overflow-wrap: anywhere;
      font-size: 12px;
      line-height: 1.5;
    }
    .inspector-view-toggle {
      display: flex;
      gap: 4px;
      margin: 8px 0 4px;
    }
    .inspector-view-btn {
      border: 1px solid var(--line);
      background: transparent;
      color: var(--muted);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 11px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .inspector-view-btn.active {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }
    .dashboard-inspector-raw {
      display: none;
      margin-top: 12px;
      padding: 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.26);
      font-family: var(--wk-font-mono);
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      overflow: auto;
      max-height: 48vh;
      color: var(--wk-text);
    }
    .dashboard-inspector[data-inspector-mode="raw"] .dashboard-inspector-raw {
      display: block;
    }
    .dashboard-inspector[data-inspector-mode="raw"] .dashboard-inspector-human,
    .dashboard-inspector[data-inspector-mode="raw"] .dashboard-inspector-agent {
      display: none;
    }
    .dashboard-inspector[data-inspector-mode="agent"] .dashboard-inspector-human[data-human-optional="1"] {
      display: none;
    }
    .dashboard-inspector[data-inspector-mode="human"] .dashboard-inspector-agent[data-agent-optional="1"] {
      display: none;
    }
    .dashboard-drawer {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.03);
      overflow: hidden;
    }
    .dashboard-drawer summary {
      list-style: none;
      cursor: pointer;
      padding: 12px 14px;
      color: var(--ink);
      font-size: 12px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      background: rgba(255, 255, 255, 0.02);
    }
    .dashboard-drawer summary::-webkit-details-marker {
      display: none;
    }
    .dashboard-drawer-body {
      padding: 14px;
      border-top: 1px solid var(--line);
    }
    .inspect-button {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.03);
      color: var(--ink);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 11px;
      cursor: pointer;
    }
    .inspect-button:hover,
    .inspect-button:focus-visible {
      border-color: var(--accent);
      color: var(--accent);
      outline: none;
    }
    @media (max-width: 1120px) {
      .hero, .cols-2, .dashboard-home-grid, .dashboard-overview-grid {
        grid-template-columns: 1fr;
      }
      .stats {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .dashboard-workbench {
        grid-template-columns: 1fr;
      }
      .dashboard-rail,
      .dashboard-inspector {
        position: static;
        min-height: auto;
      }
      .dashboard-tab-row {
        position: relative;
      }
      .dashboard-tab-row::after {
        content: "";
        position: absolute;
        right: 0;
        top: 0;
        bottom: 0;
        width: 32px;
        background: linear-gradient(90deg, transparent, var(--bg, #0a0f1a));
        pointer-events: none;
        border-radius: 0 999px 999px 0;
      }
    }
    @media (max-width: 640px) {
      .shell { padding: 16px 12px 28px; }
      .mini-grid { grid-template-columns: 1fr; }
      .learning-program-grid { grid-template-columns: 1fr; }
      .learning-program-head { flex-direction: column; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      h1 { font-size: 34px; }
    }
    #initialLoadingOverlay {
      position: fixed;
      inset: 0;
      z-index: 9999;
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 16px;
      background: var(--bg, #0a0f1a);
      color: var(--wk-text, #e0e6ed);
      font-family: var(--wk-font-sans, system-ui, sans-serif);
    }
    #initialLoadingOverlay .loading-ring {
      width: 40px;
      height: 40px;
      border: 3px solid rgba(97, 218, 251, 0.2);
      border-top-color: var(--accent, #61dafb);
      border-radius: 50%;
      animation: spin-ring 0.9s linear infinite;
    }
    @keyframes spin-ring {
      to { transform: rotate(360deg); }
    }
    .live-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .live-badge::before {
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #4cda80;
      animation: pulse-dot 1.6s ease-in-out infinite;
    }
    summary { list-style: none; }
    summary::-webkit-details-marker { display: none; }

"""

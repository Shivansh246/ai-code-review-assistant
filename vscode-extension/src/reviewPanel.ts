/**
 * reviewPanel.ts
 * Full-featured webview panel for reviewing findings.
 * Week 1:  Basic findings display.
 * Week 2:  Severity badges, clickable findings, line navigation.
 * Week 5:  XAI explanation panel.
 * Week 6:  Accept/Reject feedback UI + status display.
 * Week 7:  TTS integration + coherent UX.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import { Finding, ReviewResponse } from './types';

export class ReviewPanel {
  private static readonly viewType = 'aiReview.panel';
  private panel: vscode.WebviewPanel | undefined;
  private currentFindings: Finding[] = [];

  constructor(private readonly context: vscode.ExtensionContext) {}

  /** Open or reveal the review panel */
  open(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Beside);
      return;
    }

    this.panel = vscode.window.createWebviewPanel(
      ReviewPanel.viewType,
      '🔍 AI Code Review',
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: true }
    );

    this.panel.webview.html = this.renderHtml([], false);

    this.panel.webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case 'navigate':
          await this.navigateToLine(msg.file, msg.line);
          break;
        case 'accept':
          await vscode.commands.executeCommand('aiReview.acceptFinding', msg.id);
          break;
        case 'reject':
          await vscode.commands.executeCommand('aiReview.rejectFinding', msg.id);
          break;
        case 'explain':
          await vscode.commands.executeCommand('aiReview.explainFinding', msg.id);
          break;
        case 'fix':
          await vscode.commands.executeCommand('aiReview.generateFix', msg.id);
          break;
      }
    });

    this.panel.onDidDispose(() => {
      this.panel = undefined;
    });
  }

  /** Update panel with new review results */
  setResults(response: ReviewResponse, loading = false): void {
    this.currentFindings = response.findings;
    if (this.panel) {
      this.panel.webview.html = this.renderHtml(response.findings, loading, response);
    }
  }

  setLoading(): void {
    if (this.panel) {
      this.panel.webview.html = this.renderHtml([], true);
    }
  }

  dispose(): void {
    this.panel?.dispose();
  }

  // ── Navigation ─────────────────────────────────────────────────────────────

  private async navigateToLine(file: string, line: number): Promise<void> {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders) { return; }

    // Try to resolve relative path
    let uri: vscode.Uri | undefined;
    for (const folder of workspaceFolders) {
      const candidate = vscode.Uri.file(path.join(folder.uri.fsPath, file));
      try {
        await vscode.workspace.fs.stat(candidate);
        uri = candidate;
        break;
      } catch { /* try next */ }
    }
    if (!uri) { return; }

    const doc = await vscode.workspace.openTextDocument(uri);
    const editor = await vscode.window.showTextDocument(doc, { preserveFocus: false });
    const lineIdx = Math.max(0, line - 1);
    const range = new vscode.Range(lineIdx, 0, lineIdx, 0);
    editor.selection = new vscode.Selection(range.start, range.end);
    editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
  }

  // ── HTML rendering ─────────────────────────────────────────────────────────

  private renderHtml(
    findings: Finding[],
    loading: boolean,
    response?: ReviewResponse
  ): string {
    const severityOrder = ['critical', 'high', 'medium', 'low', 'info'];
    const sorted = [...findings].sort(
      (a, b) =>
        severityOrder.indexOf(a.severity) - severityOrder.indexOf(b.severity)
    );

    const severityBadge = (s: string) => {
      const colors: Record<string, string> = {
        critical: '#ff4444',
        high: '#ff8800',
        medium: '#ffcc00',
        low: '#44cc44',
        info: '#8888ff',
      };
      return `<span style="background:${colors[s] ?? '#888'};color:#000;padding:2px 8px;border-radius:3px;font-size:0.75rem;font-weight:bold;text-transform:uppercase;">${s}</span>`;
    };

    const findingCards = sorted
      .map(
        (f) => `
      <div class="card" data-id="${f.id}" data-severity="${f.severity}">
        <div class="card-header">
          ${severityBadge(f.severity)}
          <span class="source-tag">${f.source}</span>
          <span class="confidence">⚡ ${(f.confidence * 100).toFixed(0)}%</span>
          <span class="location" onclick="navigate('${f.file}', ${f.line_start})">
            📁 ${path.basename(f.file)}:${f.line_start}
          </span>
          ${f.feedback_status === 'accepted' ? '<span class="fb-badge accepted">✅ Accepted</span>' : ''}
          ${f.feedback_status === 'rejected' ? '<span class="fb-badge rejected">❌ Rejected</span>' : ''}
        </div>
        <div class="card-title">${escapeHtml(f.title)}</div>
        <div class="card-desc">${escapeHtml(f.description)}</div>
        ${f.cve_id ? `<div class="cve-tag">🔗 ${f.cve_id}${f.package_name ? ` — ${f.package_name}@${f.package_version ?? '?'}` : ''}</div>` : ''}
        ${f.explanation ? `<div class="explanation">🧠 <strong>XAI:</strong> ${escapeHtml(f.explanation)}</div>` : ''}
        ${f.fix_suggestion ? `<pre class="fix-code">${escapeHtml(f.fix_suggestion)}</pre>` : ''}
        <div class="card-actions">
          <button onclick="navigate('${f.file}', ${f.line_start})">Go to line</button>
          <button onclick="explain('${f.id}')">Explain</button>
          <button onclick="fix('${f.id}')">Generate Fix</button>
          <button class="accept-btn" onclick="accept('${f.id}')">✅ Accept</button>
          <button class="reject-btn" onclick="reject('${f.id}')">❌ Reject</button>
        </div>
      </div>`
      )
      .join('\n');

    const summaryHtml = response
      ? `<div class="summary">
          <span>📊 ${findings.length} finding${findings.length !== 1 ? 's' : ''}</span>
          <span>⏱ ${response.scan_duration_ms}ms</span>
          <span>🔌 Sources: ${response.sources_used.join(', ')}</span>
        </div>`
      : '';

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Code Review</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      background: var(--vscode-editor-background);
      color: var(--vscode-editor-foreground);
      padding: 16px;
    }
    h1 { font-size: 1.1rem; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
    .summary {
      display: flex; gap: 16px; flex-wrap: wrap;
      font-size: 0.82rem; opacity: 0.75;
      margin-bottom: 16px;
      padding: 8px 12px;
      background: var(--vscode-input-background);
      border-radius: 4px;
    }
    .filter-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
    .filter-btn {
      padding: 4px 12px; border: 1px solid var(--vscode-button-background);
      background: transparent; color: var(--vscode-button-background);
      border-radius: 3px; cursor: pointer; font-size: 0.8rem;
    }
    .filter-btn.active, .filter-btn:hover {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
    }
    .card {
      background: var(--vscode-input-background);
      border: 1px solid var(--vscode-input-border, #444);
      border-radius: 6px;
      padding: 14px;
      margin-bottom: 12px;
      transition: border-color 0.15s;
    }
    .card:hover { border-color: var(--vscode-focusBorder); }
    .card[data-severity="critical"] { border-left: 4px solid #ff4444; }
    .card[data-severity="high"] { border-left: 4px solid #ff8800; }
    .card[data-severity="medium"] { border-left: 4px solid #ffcc00; }
    .card[data-severity="low"] { border-left: 4px solid #44cc44; }
    .card[data-severity="info"] { border-left: 4px solid #8888ff; }
    .card-header {
      display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
      margin-bottom: 8px;
    }
    .source-tag {
      font-size: 0.72rem; padding: 1px 6px; border-radius: 10px;
      background: var(--vscode-badge-background);
      color: var(--vscode-badge-foreground);
    }
    .confidence { font-size: 0.78rem; opacity: 0.7; }
    .location {
      font-size: 0.78rem; cursor: pointer; opacity: 0.8;
      text-decoration: underline dotted;
    }
    .location:hover { opacity: 1; }
    .fb-badge { font-size: 0.78rem; padding: 1px 6px; border-radius: 3px; }
    .fb-badge.accepted { background: rgba(0,200,80,0.15); }
    .fb-badge.rejected { background: rgba(255,50,50,0.15); }
    .card-title { font-weight: bold; font-size: 0.95rem; margin-bottom: 6px; }
    .card-desc { font-size: 0.88rem; opacity: 0.85; margin-bottom: 8px; line-height: 1.5; }
    .cve-tag {
      font-size: 0.8rem; color: #ff8800;
      margin-bottom: 8px;
    }
    .explanation {
      font-size: 0.82rem; padding: 8px; border-radius: 4px;
      background: rgba(180,100,255,0.08);
      margin-bottom: 8px; line-height: 1.5;
    }
    .fix-code {
      font-family: var(--vscode-editor-font-family, monospace);
      font-size: 0.8rem;
      background: rgba(0,0,0,0.2);
      padding: 8px; border-radius: 4px;
      overflow-x: auto; white-space: pre;
      margin-bottom: 8px;
    }
    .card-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
    button {
      padding: 4px 10px; border: none; border-radius: 3px;
      cursor: pointer; font-size: 0.8rem;
      background: var(--vscode-button-secondaryBackground, #3a3a3a);
      color: var(--vscode-button-secondaryForeground, #ccc);
    }
    button:hover { background: var(--vscode-button-secondaryHoverBackground, #4a4a4a); }
    .accept-btn { background: rgba(0,200,80,0.2); color: #00c850; }
    .accept-btn:hover { background: rgba(0,200,80,0.35); }
    .reject-btn { background: rgba(255,50,50,0.2); color: #ff3232; }
    .reject-btn:hover { background: rgba(255,50,50,0.35); }
    .empty { text-align: center; opacity: 0.5; padding: 40px; }
    .loading { text-align: center; padding: 40px; font-size: 1.1rem; }
    .spinner { display: inline-block; animation: spin 1s linear infinite; }
    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    .no-findings { text-align: center; padding: 32px; color: #44cc44; }
  </style>
</head>
<body>
  <h1>🔍 AI Code Review</h1>
  ${summaryHtml}

  ${
    loading
      ? '<div class="loading"><span class="spinner">⚙️</span> Scanning…</div>'
      : findings.length === 0
      ? '<div class="no-findings">✅ No findings. Looking clean!</div>'
      : `<div class="filter-row">
          <button class="filter-btn active" onclick="filter('all')">All (${findings.length})</button>
          ${['critical','high','medium','low','info'].map(s => {
            const count = findings.filter(f => f.severity === s).length;
            return count > 0 ? `<button class="filter-btn" onclick="filter('${s}')">${s[0].toUpperCase()+s.slice(1)} (${count})</button>` : '';
          }).join('')}
        </div>
        <div id="findings-list">${findingCards}</div>`
  }

  <script>
    const vscode = acquireVsCodeApi();

    function navigate(file, line) {
      vscode.postMessage({ type: 'navigate', file, line });
    }
    function accept(id) {
      vscode.postMessage({ type: 'accept', id });
      const card = document.querySelector('[data-id="' + id + '"]');
      if (card) {
        const hdr = card.querySelector('.card-header');
        const existing = hdr.querySelector('.fb-badge');
        if (existing) { existing.remove(); }
        hdr.insertAdjacentHTML('beforeend', '<span class="fb-badge accepted">✅ Accepted</span>');
      }
    }
    function reject(id) {
      vscode.postMessage({ type: 'reject', id });
      const card = document.querySelector('[data-id="' + id + '"]');
      if (card) {
        const hdr = card.querySelector('.card-header');
        const existing = hdr.querySelector('.fb-badge');
        if (existing) { existing.remove(); }
        hdr.insertAdjacentHTML('beforeend', '<span class="fb-badge rejected">❌ Rejected</span>');
      }
    }
    function explain(id) { vscode.postMessage({ type: 'explain', id }); }
    function fix(id) { vscode.postMessage({ type: 'fix', id }); }

    function filter(sev) {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      event.target.classList.add('active');
      document.querySelectorAll('.card').forEach(c => {
        c.style.display = sev === 'all' || c.dataset.severity === sev ? '' : 'none';
      });
    }
  </script>
</body>
</html>`;
  }
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/**
 * decorationManager.ts
 * Manages inline editor decorations — severity gutter icons, underlines,
 * and hover messages for flagged lines.
 * Week 2: severity badges + line navigation.
 * Week 5: XAI token highlights added.
 */

import * as vscode from 'vscode';
import { Finding, Severity } from './types';

export class DecorationManager {
  private decorationTypes: Map<Severity, vscode.TextEditorDecorationType> =
    new Map();
  private xaiDecorationType: vscode.TextEditorDecorationType;

  constructor() {
    // Create one decoration type per severity level
    const configs: [Severity, vscode.DecorationRenderOptions][] = [
      [
        'critical',
        {
          backgroundColor: 'rgba(255,0,0,0.12)',
          borderColor: 'rgba(255,0,0,0.6)',
          border: '1px solid',
          overviewRulerColor: 'rgba(255,0,0,0.8)',
          overviewRulerLane: vscode.OverviewRulerLane.Right,
          gutterIconPath: undefined,
          after: {
            contentText: ' ⛔ CRITICAL',
            color: 'rgba(255,80,80,0.9)',
            fontWeight: 'bold',
          },
        },
      ],
      [
        'high',
        {
          backgroundColor: 'rgba(255,140,0,0.10)',
          borderColor: 'rgba(255,140,0,0.5)',
          border: '1px solid',
          overviewRulerColor: 'rgba(255,140,0,0.7)',
          overviewRulerLane: vscode.OverviewRulerLane.Right,
          after: {
            contentText: ' ⚠ HIGH',
            color: 'rgba(255,140,0,0.9)',
            fontWeight: 'bold',
          },
        },
      ],
      [
        'medium',
        {
          backgroundColor: 'rgba(255,220,0,0.08)',
          borderColor: 'rgba(255,220,0,0.4)',
          border: '1px dotted',
          overviewRulerColor: 'rgba(255,220,0,0.6)',
          overviewRulerLane: vscode.OverviewRulerLane.Center,
          after: {
            contentText: ' ⚡ MEDIUM',
            color: 'rgba(200,180,0,0.8)',
          },
        },
      ],
      [
        'low',
        {
          backgroundColor: 'rgba(0,200,80,0.06)',
          overviewRulerColor: 'rgba(0,200,80,0.5)',
          overviewRulerLane: vscode.OverviewRulerLane.Left,
          after: {
            contentText: ' ℹ LOW',
            color: 'rgba(0,180,80,0.7)',
          },
        },
      ],
      [
        'info',
        {
          overviewRulerColor: 'rgba(100,100,255,0.4)',
          overviewRulerLane: vscode.OverviewRulerLane.Left,
        },
      ],
    ];

    for (const [sev, opts] of configs) {
      this.decorationTypes.set(sev, vscode.window.createTextEditorDecorationType(opts));
    }

    // XAI highlight — Week 5
    this.xaiDecorationType = vscode.window.createTextEditorDecorationType({
      backgroundColor: 'rgba(180,100,255,0.15)',
      fontStyle: 'italic',
    });
  }

  /**
   * Apply decorations for all findings in the currently active editor.
   * Only decorates ranges that belong to the currently open file.
   */
  applyDecorations(editor: vscode.TextEditor, findings: Finding[]): void {
    const config = vscode.workspace.getConfiguration('aiReview');
    if (!config.get<boolean>('showInlineDecorations', true)) {
      this.clearAll(editor);
      return;
    }

    const filePath = editor.document.uri.fsPath;
    const relevant = findings.filter(
      (f) =>
        filePath.endsWith(f.file) ||
        f.file.endsWith(filePath.split('/').pop() ?? '')
    );

    // Group by severity
    const bySeverity = new Map<Severity, vscode.DecorationOptions[]>();

    for (const finding of relevant) {
      const lineIndex = Math.max(0, finding.line_start - 1);
      const lineEndIndex = Math.max(lineIndex, finding.line_end - 1);
      const line = editor.document.lineAt(
        Math.min(lineIndex, editor.document.lineCount - 1)
      );
      const endLine = editor.document.lineAt(
        Math.min(lineEndIndex, editor.document.lineCount - 1)
      );

      const range = new vscode.Range(
        new vscode.Position(line.lineNumber, 0),
        new vscode.Position(endLine.lineNumber, endLine.text.length)
      );

      const hoverMsg = new vscode.MarkdownString(
        `### 🔍 ${finding.title}\n\n` +
          `**Severity:** ${finding.severity.toUpperCase()}  |  **Source:** ${finding.source}  |  **Confidence:** ${(finding.confidence * 100).toFixed(0)}%\n\n` +
          `${finding.description}\n\n` +
          (finding.explanation ? `**Explanation:** ${finding.explanation}\n\n` : '') +
          (finding.fix_suggestion
            ? `**Suggested fix:**\n\`\`\`\n${finding.fix_suggestion}\n\`\`\`\n\n`
            : '') +
          `*ID: ${finding.id}*`
      );
      hoverMsg.isTrusted = true;

      const existing = bySeverity.get(finding.severity) ?? [];
      existing.push({ range, hoverMessage: hoverMsg });
      bySeverity.set(finding.severity, existing);
    }

    // Apply each severity type
    for (const [sev, decType] of this.decorationTypes) {
      editor.setDecorations(decType, bySeverity.get(sev) ?? []);
    }
  }

  /**
   * Apply XAI token-level highlights — Week 5.
   * Highlights lines identified by the XAI explanation.
   */
  applyXaiHighlights(
    editor: vscode.TextEditor,
    highlightedLines: number[]
  ): void {
    const ranges: vscode.DecorationOptions[] = [];
    for (const l of highlightedLines) {
      const lineIdx = Math.max(0, l - 1);
      if (lineIdx >= editor.document.lineCount) { continue; }
      const line = editor.document.lineAt(lineIdx);
      ranges.push({
        range: new vscode.Range(
          new vscode.Position(line.lineNumber, 0),
          new vscode.Position(line.lineNumber, line.text.length)
        ),
        hoverMessage: new vscode.MarkdownString('🧠 *Highlighted by XAI attribution*'),
      });
    }

    editor.setDecorations(this.xaiDecorationType, ranges);
  }

  clearAll(editor: vscode.TextEditor): void {
    for (const decType of this.decorationTypes.values()) {
      editor.setDecorations(decType, []);
    }
    editor.setDecorations(this.xaiDecorationType, []);
  }

  dispose(): void {
    for (const d of this.decorationTypes.values()) { d.dispose(); }
    this.xaiDecorationType.dispose();
  }
}

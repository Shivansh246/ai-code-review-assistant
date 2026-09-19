/**
 * findingsProvider.ts
 * TreeDataProvider for the "AI Security Findings" sidebar view.
 * Supports severity grouping, badge counts, and click-to-navigate.
 * Weeks 1–2 (basic), enhanced in Weeks 5–6 with XAI + feedback.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import { Finding, Severity, FeedbackStatus } from './types';

// ─── Tree node types ────────────────────────────────────────────────────────

export class SeverityGroupNode extends vscode.TreeItem {
  constructor(
    public readonly severity: Severity,
    public readonly findings: FindingNode[]
  ) {
    super(
      `${severityLabel(severity)} (${findings.length})`,
      vscode.TreeItemCollapsibleState.Expanded
    );
    this.iconPath = new vscode.ThemeIcon(severityIcon(severity), severityColor(severity));
    this.contextValue = 'severityGroup';
  }
}

export class FindingNode extends vscode.TreeItem {
  constructor(public readonly finding: Finding) {
    super(finding.title, vscode.TreeItemCollapsibleState.None);

    this.description = `${path.basename(finding.file)}:${finding.line_start}`;
    this.tooltip = new vscode.MarkdownString(
      `**${finding.title}**\n\n${finding.description}\n\n` +
        `*Source:* ${finding.source}  *Confidence:* ${(finding.confidence * 100).toFixed(0)}%` +
        (finding.cve_id ? `\n\n*CVE:* ${finding.cve_id}` : '')
    );
    this.iconPath = new vscode.ThemeIcon(
      feedbackIcon(finding.feedback_status),
      severityColor(finding.severity)
    );
    this.contextValue = 'finding';

    // Click → jump to the flagged line
    this.command = {
      command: 'aiReview.navigateToFinding',
      title: 'Go to finding',
      arguments: [finding],
    };
  }
}

type FindingsTreeNode = SeverityGroupNode | FindingNode;

// ─── Provider ───────────────────────────────────────────────────────────────

export class FindingsProvider
  implements vscode.TreeDataProvider<FindingsTreeNode>
{
  private _onDidChangeTreeData = new vscode.EventEmitter<
    FindingsTreeNode | undefined | null
  >();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private findings: Finding[] = [];
  private _loading = false;

  setFindings(findings: Finding[]): void {
    this.findings = findings;
    this._loading = false;
    this._onDidChangeTreeData.fire(undefined);
  }

  setLoading(loading: boolean): void {
    this._loading = loading;
    this._onDidChangeTreeData.fire(undefined);
  }

  updateFeedback(findingId: string, status: FeedbackStatus): void {
    const f = this.findings.find((f) => f.id === findingId);
    if (f) {
      f.feedback_status = status;
      this._onDidChangeTreeData.fire(undefined);
    }
  }

  clear(): void {
    this.findings = [];
    this._onDidChangeTreeData.fire(undefined);
  }

  getFindings(): Finding[] {
    return this.findings;
  }

  getCritical(): Finding[] {
    return this.findings.filter((f) => f.severity === 'critical');
  }

  getTreeItem(element: FindingsTreeNode): vscode.TreeItem {
    return element;
  }

  getChildren(element?: FindingsTreeNode): FindingsTreeNode[] {
    if (this._loading) {
      const item = new vscode.TreeItem('Scanning…');
      item.iconPath = new vscode.ThemeIcon('loading~spin');
      return [item as FindingsTreeNode];
    }

    if (!element) {
      // Root: group by severity
      const order: Severity[] = ['critical', 'high', 'medium', 'low', 'info'];
      return order
        .map((sev) => {
          const nodes = this.findings
            .filter((f) => f.severity === sev)
            .map((f) => new FindingNode(f));
          return nodes.length > 0 ? new SeverityGroupNode(sev, nodes) : null;
        })
        .filter((n): n is SeverityGroupNode => n !== null);
    }

    if (element instanceof SeverityGroupNode) {
      return element.findings;
    }

    return [];
  }
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function severityLabel(s: Severity): string {
  return { critical: '🔴 Critical', high: '🟠 High', medium: '🟡 Medium', low: '🟢 Low', info: '🔵 Info' }[s];
}

function severityIcon(s: Severity): string {
  return { critical: 'error', high: 'warning', medium: 'info', low: 'pass', info: 'circle-outline' }[s];
}

function severityColor(s: Severity): vscode.ThemeColor {
  const map: Record<Severity, string> = {
    critical: 'errorForeground',
    high: 'list.warningForeground',
    medium: 'editorWarning.foreground',
    low: 'terminal.ansiGreen',
    info: 'descriptionForeground',
  };
  return new vscode.ThemeColor(map[s]);
}

function feedbackIcon(status?: FeedbackStatus): string {
  if (status === 'accepted') { return 'pass'; }
  if (status === 'rejected') { return 'close'; }
  return 'bug';
}

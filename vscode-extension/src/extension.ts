/**
 * extension.ts
 * Main entry point for the AI Code Review VS Code extension.
 *
 * Registers all commands, wires together the API client, findings provider,
 * decoration manager, review panel, and voice controller.
 *
 * Week 1:  Review command, findings panel, HTTP call.
 * Week 2:  Severity badges, line navigation.
 * Week 3:  Voice commands scaffold.
 * Week 4:  Code context passed with voice.
 * Week 5:  XAI highlights, VAD gate.
 * Week 6:  Accept/Reject + voice feedback.
 * Week 7:  Full TTS loop, coherent UX.
 * Week 8:  Metrics command.
 */

import * as vscode from 'vscode';
import { ApiClient } from './apiClient';
import { FindingsProvider } from './findingsProvider';
import { DecorationManager } from './decorationManager';
import { ReviewPanel } from './reviewPanel';
import { VoiceController } from './voiceController';
import { Finding } from './types';

// ─── Extension State (module-level singletons) ────────────────────────────────

let api: ApiClient;
let findingsProvider: FindingsProvider;
let decorationManager: DecorationManager;
let reviewPanel: ReviewPanel;
let voiceController: VoiceController;

// Most recently selected finding (for context-menu commands)
let focusedFinding: Finding | undefined;

// ─── Activate ─────────────────────────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext): void {
  console.log('[aiReview] Extension activated');

  // ── Initialise singletons ─────────────────────────────────────────────────
  api = new ApiClient();
  findingsProvider = new FindingsProvider();
  decorationManager = new DecorationManager();
  reviewPanel = new ReviewPanel(context);
  voiceController = new VoiceController(api, context, findingsProvider);

  // ── Register sidebar tree view ────────────────────────────────────────────
  const treeView = vscode.window.createTreeView('aiReview.findingsView', {
    treeDataProvider: findingsProvider,
    showCollapseAll: true,
  });

  // ── Watch config changes ──────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('aiReview.backendUrl')) {
        const url = vscode.workspace
          .getConfiguration('aiReview')
          .get<string>('backendUrl', 'http://localhost:8000');
        api.updateBaseUrl(url);
      }
    })
  );

  // ── Auto-review on save (optional) ───────────────────────────────────────
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(async (doc) => {
      if (
        vscode.workspace
          .getConfiguration('aiReview')
          .get<boolean>('autoReviewOnSave', false)
      ) {
        await runReviewFile(doc);
      }
    })
  );

  // ── Re-apply decorations when active editor changes ───────────────────────
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (editor) {
        decorationManager.applyDecorations(editor, findingsProvider.getFindings());
      }
    })
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Review current file (Week 1)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('aiReview.reviewFile', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage('Open a file first to review it.');
        return;
      }
      await runReviewFile(editor.document);
    })
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Review repository (Week 1)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('aiReview.reviewRepo', async () => {
      const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!folder) {
        vscode.window.showWarningMessage('Open a workspace folder first.');
        return;
      }

      findingsProvider.setLoading(true);
      reviewPanel.setLoading();

      try {
        const response = await api.reviewRepo(folder);
        findingsProvider.setFindings(response.findings);
        reviewPanel.setResults(response);
        applyDecorationsToActiveEditor(response.findings);
        vscode.window.showInformationMessage(
          `✅ Repo scan: ${response.findings.length} findings in ${response.scan_duration_ms}ms`
        );
      } catch (err) {
        findingsProvider.setLoading(false);
        vscode.window.showErrorMessage(
          `Review failed: ${ApiClient.formatError(err)}`
        );
      }
    })
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Explain finding (Week 3 / Week 5 XAI)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand(
      'aiReview.explainFinding',
      async (findingOrId?: Finding | string) => {
        const finding = resolveFinding(findingOrId);
        if (!finding) {
          vscode.window.showWarningMessage('No finding selected to explain.');
          return;
        }

        await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: 'Getting explanation…' },
          async () => {
            try {
              const result = await api.explain(finding.id);
              finding.explanation = result.explanation;
              finding.token_attributions = result.token_attributions;

              // Show XAI highlights in editor
              const editor = vscode.window.activeTextEditor;
              if (editor && result.highlighted_lines.length > 0) {
                decorationManager.applyXaiHighlights(editor, result.highlighted_lines);
              }

              vscode.window.showInformationMessage(
                `🧠 Explanation: ${result.explanation.substring(0, 120)}…`
              );
            } catch (err) {
              vscode.window.showErrorMessage(
                `Explain failed: ${ApiClient.formatError(err)}`
              );
            }
          }
        );
      }
    )
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Show critical findings (Week 2 / Week 3 voice)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('aiReview.showCritical', async () => {
      const critical = findingsProvider.getCritical();
      if (critical.length === 0) {
        vscode.window.showInformationMessage('✅ No critical findings found.');
        return;
      }

      const picks = critical.map((f) => ({
        label: `$(error) ${f.title}`,
        description: `${f.file}:${f.line_start}`,
        detail: f.description,
        finding: f,
      }));

      const selected = await vscode.window.showQuickPick(picks, {
        placeHolder: `${critical.length} critical finding(s) — select to navigate`,
        matchOnDescription: true,
        matchOnDetail: true,
      });

      if (selected) {
        focusedFinding = selected.finding;
        findingsProvider.setFocusedFinding(selected.finding);
        await vscode.commands.executeCommand(
          'aiReview.navigateToFinding',
          selected.finding
        );
      }
    })
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Generate fix (Week 3)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand(
      'aiReview.generateFix',
      async (findingOrId?: Finding | string) => {
        const finding = resolveFinding(findingOrId);
        if (!finding) {
          vscode.window.showWarningMessage('No finding selected to fix.');
          return;
        }

        const editor = vscode.window.activeTextEditor;
        const content = editor?.document.getText() ?? '';

        await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: 'Generating fix…' },
          async () => {
            try {
              const result = await api.fix(finding.id, content);
              finding.fix_suggestion = result.fix_suggestion;

              // Show fix in diff-like quick pick
              const action = await vscode.window.showInformationMessage(
                `💡 Fix suggestion ready (confidence ${(result.confidence * 100).toFixed(0)}%)`,
                'Show Fix',
                'Dismiss'
              );
              if (action === 'Show Fix') {
                const doc = await vscode.workspace.openTextDocument({
                  content: result.fix_suggestion,
                  language: 'plaintext',
                });
                await vscode.window.showTextDocument(doc, { preview: true });
              }
            } catch (err) {
              vscode.window.showErrorMessage(
                `Fix generation failed: ${ApiClient.formatError(err)}`
              );
            }
          }
        );
      }
    )
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Navigate to finding (internal — called by tree view / panel)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand(
      'aiReview.navigateToFinding',
      async (finding: Finding) => {
        focusedFinding = finding;
        findingsProvider.setFocusedFinding(finding);
        const folders = vscode.workspace.workspaceFolders;
        if (!folders) { return; }

        // Try to open the file referenced in the finding
        for (const folder of folders) {
          const uri = vscode.Uri.joinPath(folder.uri, finding.file);
          try {
            const doc = await vscode.workspace.openTextDocument(uri);
            const editor = await vscode.window.showTextDocument(doc);
            const lineIdx = Math.max(0, finding.line_start - 1);
            const range = new vscode.Range(lineIdx, 0, lineIdx, 0);
            editor.selection = new vscode.Selection(range.start, range.end);
            editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
            return;
          } catch { /* try next folder */ }
        }
      }
    )
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Open review panel
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('aiReview.openPanel', () => {
      reviewPanel.open();
    })
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Accept finding (Week 6)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand(
      'aiReview.acceptFinding',
      async (findingOrId?: Finding | string) => {
        const finding = resolveFinding(findingOrId);
        if (!finding) {
          vscode.window.showWarningMessage('No finding selected.');
          return;
        }
        await voiceController.acceptFinding(finding.id);
        findingsProvider.updateFeedback(finding.id, 'accepted');
      }
    )
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Reject finding (Week 6)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand(
      'aiReview.rejectFinding',
      async (findingOrId?: Finding | string) => {
        const finding = resolveFinding(findingOrId);
        if (!finding) {
          vscode.window.showWarningMessage('No finding selected.');
          return;
        }
        await voiceController.rejectFinding(finding.id);
        findingsProvider.updateFeedback(finding.id, 'rejected');
      }
    )
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Start voice (Week 3+)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('aiReview.startVoice', () => {
      voiceController.start();
    })
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Stop voice (Week 5)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('aiReview.stopVoice', () => {
      voiceController.stop();
    })
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Command: Show voice metrics report (Week 8)
  // ─────────────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('aiReview.voiceMetrics', async () => {
      const report = voiceController.getMetrics().report();
      const doc = await vscode.workspace.openTextDocument({
        content: report,
        language: 'markdown',
      });
      await vscode.window.showTextDocument(doc, { preview: true });
    })
  );

  // ── Push all disposables ──────────────────────────────────────────────────
  context.subscriptions.push(
    treeView,
    { dispose: () => decorationManager.dispose() },
    { dispose: () => voiceController.dispose() },
    { dispose: () => reviewPanel.dispose() }
  );

  // ── Backend health check on startup ──────────────────────────────────────
  api.health().then((ok) => {
    if (!ok) {
      vscode.window.showWarningMessage(
        'AI Code Review: Backend is not reachable. ' +
          'Start the FastAPI server at the URL configured in settings.',
        'Open Settings'
      ).then((action) => {
        if (action === 'Open Settings') {
          vscode.commands.executeCommand(
            'workbench.action.openSettings',
            'aiReview.backendUrl'
          );
        }
      });
    } else {
      vscode.window.showInformationMessage(
        '✅ AI Code Review: Backend connected.'
      );
    }
  });
}

// ─── Deactivate ───────────────────────────────────────────────────────────────

export function deactivate(): void {
  console.log('[aiReview] Extension deactivated');
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

async function runReviewFile(doc: vscode.TextDocument): Promise<void> {
  const filePath = vscode.workspace.asRelativePath(doc.uri);
  const content = doc.getText();

  findingsProvider.setLoading(true);
  reviewPanel.open();
  reviewPanel.setLoading();

  try {
    const response = await api.review(filePath, content);
    findingsProvider.setFindings(response.findings);
    reviewPanel.setResults(response);
    applyDecorationsToActiveEditor(response.findings);

    const msg =
      response.findings.length === 0
        ? '✅ No security issues found.'
        : `⚠ ${response.findings.length} finding(s) — see the AI Review panel.`;
    vscode.window.showInformationMessage(msg);
  } catch (err) {
    findingsProvider.setLoading(false);
    vscode.window.showErrorMessage(
      `Review failed: ${ApiClient.formatError(err)}`
    );
  }
}

function applyDecorationsToActiveEditor(findings: Finding[]): void {
  const editor = vscode.window.activeTextEditor;
  if (editor) {
    decorationManager.applyDecorations(editor, findings);
  }
}

/**
 * Resolve a finding from various argument types.
 * Commands can be invoked with a Finding object (from tree view),
 * a string finding ID, or nothing (use focusedFinding).
 */
function resolveFinding(arg?: Finding | string): Finding | undefined {
  if (!arg) { return findingsProvider.getFocusedFinding() ?? focusedFinding; }
  if (typeof arg === 'string') {
    return findingsProvider.getFindings().find((f) => f.id === arg);
  }
  return arg;
}

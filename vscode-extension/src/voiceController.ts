/**
 * voiceController.ts
 * Manages the entire voice pipeline for the extension.
 *
 * Weeks 3–4: STT prototype + voice command parsing.
 * Week 5:    Local VAD + wake-word gate (heavy pipeline only runs after gate).
 * Week 6:    Accept/Reject voice actions + feedback status display.
 * Week 7:    Full voice→command→result→TTS loop.
 * Week 8:    Metrics collection (accuracy, false activations, latency).
 *
 * Architecture:
 *   Browser MediaRecorder (in webview) ──audio PCM──►  VoiceController
 *     │
 *     ▼
 *   Local VAD check  (energy-based + optional Silero)
 *     │ passes VAD?
 *     ▼
 *   Wake-word check  (keyword match on lightweight transcript)
 *     │ wake word found?
 *     ▼
 *   STT  (Web Speech API in webview or Whisper via backend)
 *     │
 *     ▼
 *   Command parser  (intent extraction)
 *     │
 *     ▼
 *   Execute command  (calls existing extension commands)
 *     │
 *     ▼
 *   TTS response  (window.showInformationMessage + optional speech synthesis)
 */

import * as vscode from 'vscode';
import { ApiClient } from './apiClient';
import {
  VoiceCommand,
  VoiceCommandType,
  ParsedVoiceIntent,
  VadResult,
  VoiceMetrics,
} from './types';

// ─── Metrics (Week 8) ───────────────────────────────────────────────────────

export class VoiceMetricsTracker {
  private metrics: VoiceMetrics = {
    total_activations: 0,
    true_activations: 0,
    false_activations: 0,
    missed_commands: 0,
    avg_latency_ms: 0,
    latency_samples: [],
  };

  recordActivation(isTrue: boolean): void {
    this.metrics.total_activations++;
    if (isTrue) { this.metrics.true_activations++; }
    else { this.metrics.false_activations++; }
  }

  recordMissedCommand(): void {
    this.metrics.missed_commands++;
  }

  recordLatency(ms: number): void {
    this.metrics.latency_samples.push(ms);
    const sum = this.metrics.latency_samples.reduce((a, b) => a + b, 0);
    this.metrics.avg_latency_ms =
      sum / this.metrics.latency_samples.length;
  }

  getMetrics(): VoiceMetrics {
    return { ...this.metrics };
  }

  report(): string {
    const m = this.metrics;
    const fpr =
      m.total_activations > 0
        ? ((m.false_activations / m.total_activations) * 100).toFixed(1)
        : '0.0';
    return (
      `## Voice Metrics Report\n\n` +
      `| Metric | Value |\n|---|---|\n` +
      `| Total activations | ${m.total_activations} |\n` +
      `| True activations | ${m.true_activations} |\n` +
      `| False activations (FAR) | ${m.false_activations} (${fpr}%) |\n` +
      `| Missed commands | ${m.missed_commands} |\n` +
      `| Avg response latency | ${m.avg_latency_ms.toFixed(0)} ms |\n`
    );
  }

  reset(): void {
    this.metrics = {
      total_activations: 0,
      true_activations: 0,
      false_activations: 0,
      missed_commands: 0,
      avg_latency_ms: 0,
      latency_samples: [],
    };
  }
}

// ─── Local VAD (Week 5) ─────────────────────────────────────────────────────

/**
 * Lightweight energy-based Voice Activity Detection.
 * Runs locally in Node — no network call required.
 * For production, swap this with Silero VAD via ONNX runtime.
 */
export function localVad(
  audioEnergyDb: number,
  vadThreshold: number
): VadResult {
  // Simple energy gate: if RMS exceeds threshold, mark as speech
  const is_speech = audioEnergyDb > vadThreshold;
  const confidence = Math.min(
    1,
    Math.max(0, (audioEnergyDb - vadThreshold + 10) / 20)
  );
  return {
    is_speech,
    confidence,
    wake_word_detected: false, // will be set by wake-word check
  };
}

// ─── Command Parser (Phase 1 Hardened) ──────────────────────────────────────

export function normalizeTranscript(raw: string): string {
  if (!raw || !raw.trim()) {
    return '';
  }

  let text = raw.trim().toLowerCase();

  // Remove common punctuation: . , ! ? : ; " ' ( )
  text = text.replace(/[.,!?:;"'()]/g, ' ');

  // Collapse multiple spaces
  text = text.replace(/\s+/g, ' ').trim();

  // Conversational prefix strip list
  const prefixes = [
    'please',
    'can you',
    'could you',
    'would you',
    'can i',
    'could i',
    'i want to',
    'i would like to',
    'i d like to',
    'id like to',
    'i need to',
    'hey',
    'hi',
    'hello',
    'let s',
    'lets',
    'tell me',
  ];

  for (const prefix of prefixes) {
    if (text === prefix) {
      continue;
    }
    if (text.startsWith(prefix + ' ')) {
      text = text.substring(prefix.length).trim();
      break;
    }
  }

  // Handle "show me" prefix specifically: "show me critical" -> "show critical"
  if (text.startsWith('show me ') && text !== 'show me') {
    text = 'show ' + text.substring(8).trim();
  }

  return text.trim();
}

interface ExtractedEntities {
  finding_index?: number;
  severity?: 'critical' | 'high';
  target?: string;
}

export function extractEntities(normalized: string): ExtractedEntities {
  const entities: ExtractedEntities = {};

  // 1. Finding index extraction (e.g. "finding 3", "issue #2", "result 5", "accept 3", "reject 2")
  const indexMatch = normalized.match(
    /\b(?:finding|issue|result|item|number|#|accept|reject|approve|dismiss|decline|fix|explain)\s*#?\s*(\d{1,3})\b/i
  );
  if (indexMatch && indexMatch[1]) {
    const idx = parseInt(indexMatch[1], 10);
    if (idx >= 1 && idx <= 999) {
      entities.finding_index = idx;
      entities.target = 'finding_index';
    }
  }

  // 2. Severity extraction
  if (/\bcritical\b/i.test(normalized)) {
    entities.severity = 'critical';
  } else if (/\bhigh\b/i.test(normalized)) {
    entities.severity = 'high';
  }

  // 3. Target resolution
  if (!entities.target) {
    if (/\b(current\s+file|this\s+file|active\s+file|file)\b/i.test(normalized)) {
      entities.target = 'current_file';
    } else if (/\b(current\s+finding|this\s+finding|focused\s+finding|this\s+issue|current\s+issue)\b/i.test(normalized)) {
      entities.target = 'current_finding';
    }
  }

  return entities;
}

/**
 * Deterministic command parser with explicit precedence:
 *
 * Precedence Order:
 * 1. generate_fix  (highest priority for fix/patch requests)
 * 2. show_critical (high priority for critical/high filtering)
 * 3. accept        (high priority for approval)
 * 4. reject        (high priority for rejection/dismissal)
 * 5. explain       (explanation requests)
 * 6. review        (file/repo security review)
 * 7. unknown       (safety fallback)
 */
export function parseVoiceIntent(rawTranscript: string): ParsedVoiceIntent {
  const normalized = normalizeTranscript(rawTranscript);
  if (!normalized) {
    return {
      command: 'unknown',
      confidence: 0.0,
      normalized_transcript: '',
    };
  }

  const entities = extractEntities(normalized);

  // 1. generate_fix
  if (/\b(fix|suggest\s+a?\s*fix|generate\s+a?\s*fix|how\s+to\s+fix|how\s+do\s+i\s+fix|patch)\b/i.test(normalized)) {
    return {
      command: 'generate_fix',
      finding_index: entities.finding_index,
      target: entities.target || (entities.finding_index ? 'finding_index' : 'current_finding'),
      confidence: 0.9,
      normalized_transcript: normalized,
    };
  }

  // 2. show_critical
  if (
    /\b(show|list|get|display|filter)\s+(?:me\s+)?(?:the\s+)?(critical|high)\b/i.test(normalized) ||
    /\b(critical|high)\s+(findings?|issues?|vulnerabilities?|alerts?)\b/i.test(normalized) ||
    /\bshow\s+critical\b/i.test(normalized) ||
    /\bshow\s+high\b/i.test(normalized)
  ) {
    return {
      command: 'show_critical',
      severity: entities.severity || 'critical',
      confidence: 0.9,
      normalized_transcript: normalized,
    };
  }

  // 3. accept
  if (/\b(accept|approve|mark\s+(?:as\s+)?accepted)\b/i.test(normalized)) {
    return {
      command: 'accept',
      finding_index: entities.finding_index,
      target: entities.target || (entities.finding_index ? 'finding_index' : 'current_finding'),
      confidence: 0.9,
      normalized_transcript: normalized,
    };
  }

  // 4. reject
  if (/\b(reject|dismiss|decline|mark\s+(?:as\s+)?rejected)\b/i.test(normalized)) {
    return {
      command: 'reject',
      finding_index: entities.finding_index,
      target: entities.target || (entities.finding_index ? 'finding_index' : 'current_finding'),
      confidence: 0.9,
      normalized_transcript: normalized,
    };
  }

  // 5. explain
  if (/\b(explain|why|why\s+is|show\s+explanation|explain\s+this|detail|details)\b/i.test(normalized)) {
    return {
      command: 'explain',
      finding_index: entities.finding_index,
      target: entities.target || (entities.finding_index ? 'finding_index' : 'current_finding'),
      confidence: 0.9,
      normalized_transcript: normalized,
    };
  }

  // 6. review
  if (/\b(review|scan|check|analyze|inspect|audit)\b/i.test(normalized)) {
    return {
      command: 'review',
      target: entities.target || 'current_file',
      confidence: 0.9,
      normalized_transcript: normalized,
    };
  }

  // 7. Safety fallback
  return {
    command: 'unknown',
    confidence: 0.0,
    normalized_transcript: normalized,
  };
}

export function parseVoiceCommand(transcript: string): VoiceCommandType {
  return parseVoiceIntent(transcript).command;
}

// ─── Voice Controller ────────────────────────────────────────────────────────

export class VoiceController {
  private statusBarItem: vscode.StatusBarItem;
  private isListening = false;
  private wakeWord: string;
  private vadThreshold: number;
  private metrics = new VoiceMetricsTracker();
  private activePanel: vscode.WebviewPanel | undefined;

  constructor(
    private readonly api: ApiClient,
    private readonly context: vscode.ExtensionContext
  ) {
    this.statusBarItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Right,
      100
    );
    this.statusBarItem.command = 'aiReview.startVoice';
    this.updateStatusBar('idle');
    this.statusBarItem.show();

    const cfg = vscode.workspace.getConfiguration('aiReview');
    this.wakeWord = cfg.get<string>('wakeWord', 'review');
    this.vadThreshold = cfg.get<number>('vadThreshold', 0.5);
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /** Start listening — opens the voice webview panel */
  start(): void {
    if (this.isListening) {
      vscode.window.showInformationMessage('Voice control is already active.');
      return;
    }

    const cfg = vscode.workspace.getConfiguration('aiReview');
    if (!cfg.get<boolean>('enableVoice', true)) {
      vscode.window.showWarningMessage(
        'Voice control is disabled in settings (aiReview.enableVoice).'
      );
      return;
    }

    this.isListening = true;
    this.updateStatusBar('listening');
    this.openVoicePanel();
    vscode.window.showInformationMessage(
      `🎙 Voice active. Say "${this.wakeWord}" to trigger a command.`
    );
  }

  /** Stop listening */
  stop(): void {
    this.isListening = false;
    this.updateStatusBar('idle');
    this.activePanel?.dispose();
    vscode.window.showInformationMessage('🔇 Voice control stopped.');
  }

  /**
   * Process a recognised transcript from the webview.
   * Called when the webview posts a message.
   */
  async processTranscript(
    transcript: string,
    audioEnergyDb: number
  ): Promise<void> {
    const t0 = Date.now();

    // ── Step 1: Local VAD gate ────────────────────────────────────────────
    const cfg = vscode.workspace.getConfiguration('aiReview');
    this.vadThreshold = cfg.get<number>('vadThreshold', 0.5);
    const vad = localVad(audioEnergyDb, this.vadThreshold);

    if (!vad.is_speech) {
      // Background noise — skip silently
      return;
    }

    // ── Step 2: Wake-word gate ────────────────────────────────────────────
    const wakeWordFound = transcript
      .toLowerCase()
      .includes(this.wakeWord.toLowerCase());

    if (!wakeWordFound) {
      this.metrics.recordActivation(false);
      return;
    }

    this.metrics.recordActivation(true);
    this.updateStatusBar('processing');

    // ── Step 3: Parse intent ──────────────────────────────────────────────
    const commandType = parseVoiceCommand(transcript);

    if (commandType === 'unknown') {
      this.metrics.recordMissedCommand();
      this.speak('I didn\'t recognise that command. Try: review, explain, show critical, generate fix, accept, or reject.');
      this.updateStatusBar('listening');
      return;
    }

    // ── Step 4: Build command payload + execute ───────────────────────────
    const editor = vscode.window.activeTextEditor;
    const cmd: VoiceCommand = {
      transcript,
      command: commandType,
      file_path: editor?.document.uri.fsPath,
      code_context: editor?.document.getText(),
    };

    try {
      await this.executeVoiceCommand(commandType, cmd);
      const latency = Date.now() - t0;
      this.metrics.recordLatency(latency);
    } catch (err) {
      vscode.window.showErrorMessage(
        `Voice command failed: ${ApiClient.formatError(err)}`
      );
    } finally {
      this.updateStatusBar('listening');
    }
  }

  /** Accept a finding by voice or button — Week 6 */
  async acceptFinding(findingId: string): Promise<void> {
    await this.api.sendFeedback({
      finding_id: findingId,
      status: 'accepted',
      timestamp: new Date().toISOString(),
    });
    this.speak('Finding accepted. The model will prioritise similar findings.');
    vscode.window.showInformationMessage(`✅ Finding accepted: ${findingId}`);
  }

  /** Reject a finding by voice or button — Week 6 */
  async rejectFinding(findingId: string): Promise<void> {
    await this.api.sendFeedback({
      finding_id: findingId,
      status: 'rejected',
      timestamp: new Date().toISOString(),
    });
    this.speak('Finding rejected. False positive recorded.');
    vscode.window.showInformationMessage(`❌ Finding rejected: ${findingId}`);
  }

  getMetrics(): VoiceMetricsTracker {
    return this.metrics;
  }

  dispose(): void {
    this.statusBarItem.dispose();
    this.activePanel?.dispose();
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  private async executeVoiceCommand(
    type: VoiceCommandType,
    cmd: VoiceCommand
  ): Promise<void> {
    switch (type) {
      case 'review':
        this.speak('Starting code review.');
        await vscode.commands.executeCommand('aiReview.reviewFile');
        break;

      case 'explain':
        this.speak('Explaining the top finding.');
        await vscode.commands.executeCommand('aiReview.explainFinding');
        break;

      case 'show_critical':
        this.speak('Showing critical findings.');
        await vscode.commands.executeCommand('aiReview.showCritical');
        break;

      case 'generate_fix':
        this.speak('Generating a fix suggestion.');
        await vscode.commands.executeCommand('aiReview.generateFix');
        break;

      case 'accept':
        this.speak('Which finding do you want to accept? Opening findings panel.');
        await vscode.commands.executeCommand('aiReview.openPanel');
        break;

      case 'reject':
        this.speak('Which finding do you want to reject? Opening findings panel.');
        await vscode.commands.executeCommand('aiReview.openPanel');
        break;
    }

    // Also send to backend for multimodal context (Week 4)
    try {
      await this.api.sendVoiceCommand(cmd);
    } catch {
      // Non-critical — continue even if backend call fails
    }
  }

  /** Text-to-Speech via webview postMessage — Week 7 */
  private speak(text: string): void {
    this.activePanel?.webview.postMessage({ type: 'speak', text });
  }

  private updateStatusBar(state: 'idle' | 'listening' | 'processing'): void {
    const icons = { idle: '$(mic)', listening: '$(radio-tower)', processing: '$(loading~spin)' };
    const labels = { idle: 'AI Voice', listening: 'Listening…', processing: 'Processing…' };
    this.statusBarItem.text = `${icons[state]} ${labels[state]}`;
    this.statusBarItem.backgroundColor =
      state === 'listening'
        ? new vscode.ThemeColor('statusBarItem.warningBackground')
        : undefined;
  }

  /** Open the voice webview panel that handles microphone access + Web Speech API */
  private openVoicePanel(): void {
    this.activePanel = vscode.window.createWebviewPanel(
      'aiReviewVoice',
      '🎙 Voice Commands',
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true }
    );

    this.activePanel.webview.html = this.getVoiceWebviewHtml();

    // Receive transcripts from the webview
    this.activePanel.webview.onDidReceiveMessage(async (msg) => {
      if (msg.type === 'transcript') {
        await this.processTranscript(
          msg.transcript as string,
          msg.energyDb as number
        );
      }
    });

    this.activePanel.onDidDispose(() => {
      this.isListening = false;
      this.updateStatusBar('idle');
      this.activePanel = undefined;
    });
  }

  private getVoiceWebviewHtml(): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Voice Commands</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--vscode-font-family);
      background: var(--vscode-editor-background);
      color: var(--vscode-editor-foreground);
      display: flex; flex-direction: column; align-items: center;
      padding: 32px 16px; gap: 24px; min-height: 100vh;
    }
    h2 { font-size: 1.2rem; opacity: 0.9; }
    #status {
      font-size: 3rem;
      animation: none;
      transition: transform 0.2s;
    }
    #status.active { animation: pulse 1s infinite; }
    @keyframes pulse {
      0%, 100% { transform: scale(1); opacity: 1; }
      50% { transform: scale(1.15); opacity: 0.7; }
    }
    #transcript {
      width: 100%; max-width: 420px;
      background: var(--vscode-input-background);
      border: 1px solid var(--vscode-input-border);
      border-radius: 6px;
      padding: 10px 14px;
      font-size: 0.95rem;
      min-height: 48px;
      word-break: break-word;
    }
    #command-display {
      font-weight: bold;
      color: var(--vscode-terminal-ansiGreen);
      font-size: 1rem;
      min-height: 1.2em;
    }
    #energy-bar {
      width: 100%; max-width: 420px;
      height: 8px;
      background: var(--vscode-input-background);
      border-radius: 4px;
      overflow: hidden;
    }
    #energy-fill {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #00c853, #ff6d00, #d50000);
      transition: width 0.1s;
    }
    .hint {
      font-size: 0.8rem;
      opacity: 0.6;
      text-align: center;
      max-width: 340px;
    }
    button {
      padding: 8px 20px;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      font-size: 0.9rem;
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
    }
    button:hover { background: var(--vscode-button-hoverBackground); }
  </style>
</head>
<body>
  <h2>AI Code Review — Voice Control</h2>
  <div id="status">🎙</div>
  <div id="transcript">Waiting for speech…</div>
  <div id="energy-bar"><div id="energy-fill"></div></div>
  <div id="command-display"></div>
  <p class="hint">
    Say <strong>"review"</strong>, <strong>"explain"</strong>,
    <strong>"show critical"</strong>, <strong>"generate fix"</strong>,
    <strong>"accept"</strong>, or <strong>"reject"</strong>.
  </p>
  <button onclick="toggleListen()" id="toggleBtn">Stop Listening</button>

  <script>
    const vscode = acquireVsCodeApi();
    let recognition;
    let listening = true;
    let audioCtx, analyser, micStream;

    // ── TTS (Week 7) ──────────────────────────────────────────────────────
    window.addEventListener('message', e => {
      const msg = e.data;
      if (msg.type === 'speak' && msg.text) {
        const utt = new SpeechSynthesisUtterance(msg.text);
        utt.rate = 1.1;
        window.speechSynthesis.speak(utt);
      }
    });

    // ── Energy / VAD ──────────────────────────────────────────────────────
    async function startAudioAnalysis() {
      try {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioCtx = new AudioContext();
        const source = audioCtx.createMediaStreamSource(micStream);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        updateEnergy();
      } catch(e) {
        console.warn('Mic access denied, energy bar disabled:', e);
      }
    }

    function getEnergyDb() {
      if (!analyser) { return -60; }
      const buf = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(buf);
      const rms = Math.sqrt(buf.reduce((s, v) => s + v*v, 0) / buf.length);
      return 20 * Math.log10(Math.max(rms, 1e-9));
    }

    function updateEnergy() {
      const db = getEnergyDb();
      const pct = Math.max(0, Math.min(100, (db + 60) / 60 * 100));
      document.getElementById('energy-fill').style.width = pct + '%';
      if (listening) { requestAnimationFrame(updateEnergy); }
    }

    // ── Web Speech API ────────────────────────────────────────────────────
    function startRecognition() {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        document.getElementById('transcript').textContent =
          'Speech recognition not supported. Use Chrome/Edge.';
        return;
      }

      recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = 'en-US';

      recognition.onstart = () => {
        document.getElementById('status').classList.add('active');
      };

      recognition.onresult = (event) => {
        let final = '';
        let interim = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const t = event.results[i][0].transcript;
          if (event.results[i].isFinal) { final += t; }
          else { interim += t; }
        }
        const display = final || interim;
        document.getElementById('transcript').textContent = display;

        if (final) {
          const energyDb = getEnergyDb();
          vscode.postMessage({ type: 'transcript', transcript: final.trim(), energyDb });
        }
      };

      recognition.onerror = (e) => {
        if (e.error !== 'no-speech') {
          document.getElementById('transcript').textContent = 'Error: ' + e.error;
        }
      };

      recognition.onend = () => {
        if (listening) { recognition.start(); } // auto-restart
      };

      recognition.start();
    }

    function toggleListen() {
      listening = !listening;
      document.getElementById('toggleBtn').textContent =
        listening ? 'Stop Listening' : 'Start Listening';
      document.getElementById('status').classList.toggle('active', listening);
      if (listening) { recognition && recognition.start(); }
      else { recognition && recognition.stop(); }
    }

    startAudioAnalysis();
    startRecognition();
  </script>
</body>
</html>`;
  }
}

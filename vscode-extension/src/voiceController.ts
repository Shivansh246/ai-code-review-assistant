/**
 * voiceController.ts
 * Manages the entire voice pipeline for the extension.
 *
 * Phase 1: Hardened deterministic intent parser.
 * Phase 2: Local Pre-STT energy-based VAD gate (SpeechRecognition stopped during silence).
 * Phase 3: Robust Speech-to-Text (STT) lifecycle with session isolation, stale callback
 *          guards, single-dispatch invariants, and safe error/end recovery.
 *
 * Architecture:
 *   Browser MediaRecorder (in webview) ──audio stream──►  AudioContext / AnalyserNode
 *     │
 *     ▼
 *   Local Hysteresis VAD Gate  (Energy dB moving average + ON/OFF thresholds)
 *     │ passes VAD? (Speech ON)
 *     ▼
 *   STT Session Initiation  (Unique Session ID created; SpeechRecognition starts)
 *     │
 *     ▼
 *   STT Lifecycle Manager  (Filters stale callbacks, interim vs final, single dispatch)
 *     │
 *     ▼
 *   Wake-word Check  (Transcript-level keyword filter)
 *     │
 *     ▼
 *   Command Parser  (Deterministic precedence intent extraction)
 *     │
 *     ▼
 *   Execute Command  (Dispatches existing VS Code commands)
 */

import * as vscode from 'vscode';
import { ApiClient } from './apiClient';
import {
  VoiceCommand,
  VoiceCommandType,
  ParsedVoiceIntent,
  VadResult,
  VadState,
  VadConfig,
  VoiceMetrics,
  SttSessionState,
  SttSession,
} from './types';

// ─── Metrics (Phase 2 & 3 Extended) ─────────────────────────────────────────

export class VoiceMetricsTracker {
  private metrics: VoiceMetrics = {
    total_activations: 0,
    true_activations: 0,
    false_activations: 0,
    missed_commands: 0,
    avg_latency_ms: 0,
    latency_samples: [],
    vad_activations: 0,
    vad_false_activations: 0,
    stt_active_ms: 0,
    stt_inactive_ms: 0,
  };

  recordActivation(isTrue: boolean): void {
    this.metrics.total_activations++;
    if (isTrue) { this.metrics.true_activations++; }
    else { this.metrics.false_activations++; }
  }

  recordVadActivation(isFalse: boolean = false): void {
    this.metrics.vad_activations = (this.metrics.vad_activations || 0) + 1;
    if (isFalse) {
      this.metrics.vad_false_activations = (this.metrics.vad_false_activations || 0) + 1;
    }
  }

  recordSttState(activeMs: number, inactiveMs: number): void {
    this.metrics.stt_active_ms = (this.metrics.stt_active_ms || 0) + activeMs;
    this.metrics.stt_inactive_ms = (this.metrics.stt_inactive_ms || 0) + inactiveMs;
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
      `| VAD Activations | ${m.vad_activations || 0} |\n` +
      `| VAD False Activations | ${m.vad_false_activations || 0} |\n` +
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
      vad_activations: 0,
      vad_false_activations: 0,
      stt_active_ms: 0,
      stt_inactive_ms: 0,
    };
  }
}

// ─── Hysteresis VAD Engine (Phase 2) ────────────────────────────────────────

/**
 * Deterministic energy-based Voice Activity Detection engine with
 * temporal frame smoothing, dual-threshold hysteresis, and hangover timing.
 */
export class HysteresisVadEngine {
  private state: VadState = 'IDLE';
  private config: VadConfig;
  private energyBuffer: number[] = [];
  private consecutiveOnMs: number = 0;
  private consecutiveOffMs: number = 0;
  private isSpeechActive: boolean = false;

  constructor(config?: Partial<VadConfig>) {
    this.config = {
      onThresholdDb: config?.onThresholdDb ?? -30.0,
      offThresholdDb: config?.offThresholdDb ?? -42.0,
      onDurationMs: config?.onDurationMs ?? 80,
      offDurationMs: config?.offDurationMs ?? 400,
      frameDurationMs: config?.frameDurationMs ?? 16.6,
      smoothingWindowSize: config?.smoothingWindowSize ?? 5,
    };
  }

  public updateConfig(newConfig: Partial<VadConfig>): void {
    if (newConfig.onThresholdDb !== undefined) { this.config.onThresholdDb = newConfig.onThresholdDb; }
    if (newConfig.offThresholdDb !== undefined) { this.config.offThresholdDb = newConfig.offThresholdDb; }
    if (newConfig.onDurationMs !== undefined) { this.config.onDurationMs = newConfig.onDurationMs; }
    if (newConfig.offDurationMs !== undefined) { this.config.offDurationMs = newConfig.offDurationMs; }
    if (newConfig.frameDurationMs !== undefined) { this.config.frameDurationMs = newConfig.frameDurationMs; }
    if (newConfig.smoothingWindowSize !== undefined) { this.config.smoothingWindowSize = newConfig.smoothingWindowSize; }
  }

  public reset(): void {
    this.state = 'IDLE';
    this.energyBuffer = [];
    this.consecutiveOnMs = 0;
    this.consecutiveOffMs = 0;
    this.isSpeechActive = false;
  }

  public getState(): VadState {
    return this.state;
  }

  public setState(newState: VadState): void {
    this.state = newState;
  }

  public getConfig(): VadConfig {
    return { ...this.config };
  }

  /**
   * Process a single frame of raw audio energy in dB.
   */
  public processFrame(rawEnergyDb: number, frameDurationMs?: number): VadResult {
    const deltaMs = frameDurationMs ?? this.config.frameDurationMs ?? 16.6;

    // 1. Moving Average Temporal Smoothing
    const windowSize = this.config.smoothingWindowSize ?? 5;
    const nominalWindowMs = windowSize * (this.config.frameDurationMs ?? 16.6);

    // If frame delta exceeds smoothing window duration, flush stale window
    if (deltaMs >= nominalWindowMs) {
      this.energyBuffer = [rawEnergyDb];
    } else {
      this.energyBuffer.push(rawEnergyDb);
      if (this.energyBuffer.length > windowSize) {
        this.energyBuffer.shift();
      }
    }
    const smoothedDb =
      this.energyBuffer.reduce((sum, val) => sum + val, 0) / this.energyBuffer.length;

    // 2. Dual Threshold & Timing Accrual
    if (smoothedDb >= this.config.onThresholdDb) {
      this.consecutiveOnMs += deltaMs;
      this.consecutiveOffMs = 0;
    } else if (smoothedDb <= this.config.offThresholdDb) {
      this.consecutiveOffMs += deltaMs;
      this.consecutiveOnMs = 0;
    } else {
      // In deadband between OFF and ON thresholds
      this.consecutiveOnMs = 0;
      this.consecutiveOffMs = 0;
    }

    // 3. State Machine Transitions
    if (!this.isSpeechActive) {
      if (this.consecutiveOnMs >= this.config.onDurationMs) {
        this.isSpeechActive = true;
        this.state = 'VOICE_DETECTED';
      } else {
        this.state = 'IDLE';
      }
    } else {
      if (this.consecutiveOffMs >= this.config.offDurationMs) {
        this.isSpeechActive = false;
        this.state = 'COMMAND_PROCESSING';
      } else {
        this.state = 'ACTIVE_LISTENING';
      }
    }

    // 4. Calculate Confidence (0.0 to 1.0)
    const range = Math.max(1, this.config.onThresholdDb - this.config.offThresholdDb);
    const confidence = Math.min(
      1.0,
      Math.max(0.0, (smoothedDb - this.config.offThresholdDb) / range)
    );

    return {
      is_speech: this.isSpeechActive,
      confidence,
      wake_word_detected: false,
      state: this.state,
      energy_db: rawEnergyDb,
      smoothed_energy_db: smoothedDb,
    };
  }
}

/**
 * Lightweight energy-based Voice Activity Detection.
 * Supports legacy single threshold or dual-threshold dB hysteresis.
 */
export function localVad(
  audioEnergyDb: number,
  vadThreshold: number = -30.0
): VadResult {
  let onThresholdDb = vadThreshold;
  if (vadThreshold >= 0.0 && vadThreshold <= 1.0) {
    onThresholdDb = -60.0 + vadThreshold * 60.0;
  }

  const offThresholdDb = onThresholdDb - 12.0;
  const is_speech = audioEnergyDb >= onThresholdDb;
  const confidence = Math.min(
    1,
    Math.max(0, (audioEnergyDb - offThresholdDb) / 20)
  );

  return {
    is_speech,
    confidence,
    wake_word_detected: false,
    energy_db: audioEnergyDb,
    smoothed_energy_db: audioEnergyDb,
    state: is_speech ? 'ACTIVE_LISTENING' : 'IDLE',
  };
}

// ─── STT Lifecycle Manager (Phase 3) ────────────────────────────────────────

/**
 * Deterministic manager for the SpeechRecognition lifecycle.
 * Ensures session isolation, guards against stale callbacks, enforces that at most
 * one voice command is dispatched per session, and safely handles error and completion states.
 */
export class SttLifecycleManager {
  private currentSession: SttSession | null = null;
  private sessionCounter: number = 0;
  private isProcessingCommand: boolean = false;

  /**
   * Start a new STT session triggered by VAD activation.
   * Returns the new session or null if a session is already active or a command is processing.
   */
  public startSession(timestamp: number = Date.now()): SttSession | null {
    if (this.isProcessingCommand) {
      return null;
    }
    if (
      this.currentSession &&
      (this.currentSession.state === 'STARTING' ||
        this.currentSession.state === 'LISTENING' ||
        this.currentSession.state === 'PROCESSING')
    ) {
      return null; // Prevent duplicate session start while already active
    }

    this.sessionCounter++;
    const session: SttSession = {
      id: this.sessionCounter,
      state: 'STARTING',
      hasDispatched: false,
      transcript: '',
      createdAt: timestamp,
    };
    this.currentSession = session;
    return session;
  }

  /**
   * Called when SpeechRecognition successfully starts (`onstart`).
   * Ignores stale callbacks from obsolete sessions.
   */
  public onRecognitionStart(sessionId: number): boolean {
    if (!this.currentSession || this.currentSession.id !== sessionId) {
      return false; // Stale callback
    }
    if (this.currentSession.state !== 'STARTING') {
      return false;
    }
    this.currentSession.state = 'LISTENING';
    return true;
  }

  /**
   * Called on interim recognition results (`onresult` with `!isFinal`).
   * Updates interim transcript but NEVER allows command dispatch.
   */
  public onInterimResult(
    sessionId: number,
    interimText: string
  ): { accepted: boolean; interimText?: string } {
    if (!this.currentSession || this.currentSession.id !== sessionId) {
      return { accepted: false }; // Stale
    }
    if (this.currentSession.state !== 'LISTENING' && this.currentSession.state !== 'STARTING') {
      return { accepted: false };
    }
    this.currentSession.transcript = interimText;
    return { accepted: true, interimText };
  }

  /**
   * Called on final recognition results (`onresult` with `isFinal`).
   * Ensures:
   * 1. Stale sessions are ignored.
   * 2. Empty final results are ignored.
   * 3. At most one command is dispatched per session (`hasDispatched` guard).
   */
  public onFinalResult(
    sessionId: number,
    finalText: string
  ): { accepted: boolean; canDispatch: boolean; transcript: string } {
    if (!this.currentSession || this.currentSession.id !== sessionId) {
      return { accepted: false, canDispatch: false, transcript: '' }; // Stale callback
    }

    const trimmed = (finalText || '').trim();
    if (!trimmed) {
      return { accepted: false, canDispatch: false, transcript: '' }; // Empty final ignored
    }

    if (this.currentSession.hasDispatched) {
      return { accepted: true, canDispatch: false, transcript: trimmed }; // Already dispatched for this session
    }

    if (this.currentSession.state !== 'LISTENING' && this.currentSession.state !== 'STARTING') {
      return { accepted: false, canDispatch: false, transcript: '' };
    }

    this.currentSession.hasDispatched = true;
    this.currentSession.transcript = trimmed;
    this.currentSession.state = 'PROCESSING';
    this.isProcessingCommand = true;

    return { accepted: true, canDispatch: true, transcript: trimmed };
  }

  /**
   * Called on SpeechRecognition error (`onerror`).
   * Cleanly transitions session to ERROR state and unlocks command processing.
   */
  public onRecognitionError(sessionId: number, error: string): boolean {
    if (!this.currentSession || this.currentSession.id !== sessionId) {
      return false; // Stale error
    }
    this.currentSession.state = 'ERROR';
    this.currentSession.error = error;
    this.currentSession.endedAt = Date.now();
    this.isProcessingCommand = false;
    return true;
  }

  /**
   * Called when SpeechRecognition ends (`onend`).
   * Does NOT auto-restart.
   * If session is currently PROCESSING a command, preserves PROCESSING state
   * until finishCommandProcessing() is called.
   * If not PROCESSING and not in ERROR (e.g. LISTENING without dispatch), marks COMPLETED.
   */
  public onRecognitionEnd(sessionId: number): boolean {
    if (!this.currentSession || this.currentSession.id !== sessionId) {
      return false; // Stale onend
    }
    if (this.currentSession.state !== 'PROCESSING' && this.currentSession.state !== 'ERROR') {
      this.currentSession.state = 'COMPLETED';
    }
    this.currentSession.endedAt = Date.now();
    return true;
  }

  /**
   * Complete command processing after dispatch finishes or fails.
   */
  public finishCommandProcessing(sessionId?: number): void {
    if (this.currentSession && sessionId !== undefined && this.currentSession.id !== sessionId) {
      // Stale completion from an earlier session; do not modify active session
      return;
    }
    this.isProcessingCommand = false;
    if (this.currentSession && (!sessionId || this.currentSession.id === sessionId)) {
      if (this.currentSession.state === 'PROCESSING') {
        this.currentSession.state = 'COMPLETED';
      }
    }
  }

  public getCurrentSession(): SttSession | null {
    return this.currentSession ? { ...this.currentSession } : null;
  }

  public isBusy(): boolean {
    return (
      this.isProcessingCommand ||
      (this.currentSession !== null &&
        (this.currentSession.state === 'STARTING' ||
          this.currentSession.state === 'LISTENING' ||
          this.currentSession.state === 'PROCESSING'))
    );
  }

  public reset(): void {
    this.currentSession = null;
    this.isProcessingCommand = false;
  }
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

// ─── Voice Controller (Phase 2 & 3 Robust Lifecycle) ────────────────────────

export class VoiceController {
  private statusBarItem: vscode.StatusBarItem;
  private isListening = false;
  private wakeWord: string;
  private vadEngine: HysteresisVadEngine;
  private sttLifecycle = new SttLifecycleManager();
  private lastProcessedSessionId: number = -1;
  private metrics = new VoiceMetricsTracker();
  private activePanel: vscode.WebviewPanel | undefined;

  constructor(
    private readonly api: ApiClient,
    private readonly context: vscode.ExtensionContext,
    private readonly findingsProvider?: {
      getFindings(): import('./types').Finding[];
      getFocusedFinding?(): import('./types').Finding | undefined;
    }
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
    const onDb = cfg.get<number>('vadOnThresholdDb', -30.0);
    const offDb = cfg.get<number>('vadOffThresholdDb', -42.0);
    const onMs = cfg.get<number>('vadOnDurationMs', 80);
    const offMs = cfg.get<number>('vadOffDurationMs', 400);

    this.vadEngine = new HysteresisVadEngine({
      onThresholdDb: onDb,
      offThresholdDb: offDb,
      onDurationMs: onMs,
      offDurationMs: offMs,
    });
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
      `🎙 Voice active (VAD + STT Lifecycle Gate). Say "${this.wakeWord}" to trigger a command.`
    );
  }

  /** Stop listening */
  stop(): void {
    this.isListening = false;
    this.updateStatusBar('idle');
    this.sttLifecycle.reset();
    this.activePanel?.dispose();
    vscode.window.showInformationMessage('🔇 Voice control stopped.');
  }

  /**
   * Process a recognised transcript from the webview.
   * Called when the webview posts a message.
   */
  async processTranscript(
    transcript: string,
    audioEnergyDb: number,
    sessionId?: number
  ): Promise<void> {
    if (!transcript || !transcript.trim()) {
      return; // Ignore empty/whitespace transcripts
    }

    // Invariant: at most one voice command dispatched per STT session
    if (sessionId !== undefined) {
      if (this.lastProcessedSessionId === sessionId) {
        return; // Guard against duplicate dispatch for the same session
      }
      this.lastProcessedSessionId = sessionId;
    }

    const t0 = Date.now();

    // ── Step 1: Local VAD check ────────────────────────────────────────────
    const cfg = vscode.workspace.getConfiguration('aiReview');
    const onDb = cfg.get<number>('vadOnThresholdDb', -30.0);
    const offDb = cfg.get<number>('vadOffThresholdDb', -42.0);

    this.vadEngine.updateConfig({
      onThresholdDb: onDb,
      offThresholdDb: offDb,
    });

    const vad = this.vadEngine.processFrame(audioEnergyDb);
    this.metrics.recordVadActivation(!vad.is_speech);

    if (!vad.is_speech) {
      // Background noise / silence — skip silently
      this.sttLifecycle.finishCommandProcessing(sessionId);
      this.notifyWebviewCommandCompleted(sessionId);
      return;
    }

    // ── Step 2: Transcript Wake-word Gate ─────────────────────────────────
    const wakeWordFound = transcript
      .toLowerCase()
      .includes(this.wakeWord.toLowerCase());

    if (!wakeWordFound) {
      this.metrics.recordActivation(false);
      this.metrics.recordVadActivation(true); // VAD passed but wake word missing
      this.sttLifecycle.finishCommandProcessing(sessionId);
      this.notifyWebviewCommandCompleted(sessionId);
      return;
    }

    this.metrics.recordActivation(true);
    this.updateStatusBar('processing');

    // ── Step 3: Parse intent (full intent with entities, not just command type) ─
    const intent = parseVoiceIntent(transcript);

    if (intent.command === 'unknown') {
      this.metrics.recordMissedCommand();
      this.speak('I didn\'t recognise that command. Try: review, explain, show critical, generate fix, accept, or reject.');
      this.updateStatusBar('listening');
      this.sttLifecycle.finishCommandProcessing(sessionId);
      this.notifyWebviewCommandCompleted(sessionId);
      return;
    }

    // ── Step 4: Build command payload + execute ───────────────────────────
    const editor = vscode.window.activeTextEditor;
    const cmd: VoiceCommand = {
      transcript,
      command: intent.command,
      file_path: editor?.document.uri.fsPath,
      code_context: editor?.document.getText(),
      finding_index: intent.finding_index,
      severity: intent.severity,
      target: intent.target,
    };

    try {
      await this.executeVoiceCommand(intent, cmd);
      const latency = Date.now() - t0;
      this.metrics.recordLatency(latency);
    } catch (err) {
      vscode.window.showErrorMessage(
        `Voice command failed: ${ApiClient.formatError(err)}`
      );
    } finally {
      this.sttLifecycle.finishCommandProcessing(sessionId);
      this.notifyWebviewCommandCompleted(sessionId);
      this.updateStatusBar('listening');
    }
  }

  /** Accept a finding by voice or button */
  async acceptFinding(findingId: string): Promise<void> {
    await this.api.sendFeedback({
      finding_id: findingId,
      status: 'accepted',
      timestamp: new Date().toISOString(),
    });
    this.speak('Finding accepted. The model will prioritise similar findings.');
    vscode.window.showInformationMessage(`✅ Finding accepted: ${findingId}`);
  }

  /** Reject a finding by voice or button */
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

  getSttLifecycle(): SttLifecycleManager {
    return this.sttLifecycle;
  }

  dispose(): void {
    this.statusBarItem.dispose();
    this.activePanel?.dispose();
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  private async executeVoiceCommand(
    intent: ParsedVoiceIntent,
    cmd: VoiceCommand
  ): Promise<void> {
    switch (intent.command) {
      case 'review':
        this.speak('Starting code review.');
        await vscode.commands.executeCommand('aiReview.reviewFile');
        break;

      case 'explain': {
        const finding = this.resolveTargetFinding(intent);
        if (!finding) {
          this.speak('No finding is selected. Please navigate to a finding first, or say "explain finding" followed by a number.');
          vscode.window.showWarningMessage('Voice: No finding selected to explain.');
        } else {
          this.speak(`Explaining finding: ${finding.title}.`);
          await vscode.commands.executeCommand('aiReview.explainFinding', finding);
        }
        break;
      }

      case 'show_critical': {
        const severity = intent.severity ?? 'critical';
        this.speak(`Showing ${severity} findings.`);
        await vscode.commands.executeCommand('aiReview.showCritical');
        break;
      }

      case 'generate_fix': {
        const finding = this.resolveTargetFinding(intent);
        if (!finding) {
          this.speak('No finding is selected. Please navigate to a finding first, or say "fix finding" followed by a number.');
          vscode.window.showWarningMessage('Voice: No finding selected to fix.');
        } else {
          this.speak(`Generating a fix for: ${finding.title}.`);
          await vscode.commands.executeCommand('aiReview.generateFix', finding);
        }
        break;
      }

      case 'accept': {
        const finding = this.resolveTargetFinding(intent);
        if (!finding) {
          this.speak('No finding is selected. Please say "accept finding" followed by a number, or navigate to a finding first.');
          vscode.window.showWarningMessage('Voice: No finding selected to accept.');
        } else {
          this.speak(`Accepting finding: ${finding.title}.`);
          await vscode.commands.executeCommand('aiReview.acceptFinding', finding);
        }
        break;
      }

      case 'reject': {
        const finding = this.resolveTargetFinding(intent);
        if (!finding) {
          this.speak('No finding is selected. Please say "reject finding" followed by a number, or navigate to a finding first.');
          vscode.window.showWarningMessage('Voice: No finding selected to reject.');
        } else {
          this.speak(`Rejecting finding: ${finding.title}.`);
          await vscode.commands.executeCommand('aiReview.rejectFinding', finding);
        }
        break;
      }
    }

    try {
      await this.api.sendVoiceCommand(cmd);
    } catch {
      // Non-critical — continue even if backend call fails
    }
  }

  /**
   * Resolve a Finding object from a parsed voice intent.
   *
   * Resolution order:
   * 1. `finding_index` (1-based, user-visible) — converts to 0-based array index.
   * 2. `target === 'current_finding'` — uses the currently focused finding (set by
   *    the last `aiReview.navigateToFinding` invocation in extension.ts).
   * 3. No target specified — returns undefined; caller must provide voice feedback.
   *
   * The 1-based → 0-based conversion is performed here and ONLY here.
   */
  private resolveTargetFinding(intent: ParsedVoiceIntent): import('./types').Finding | undefined {
    const findings = this.findingsProvider?.getFindings() ?? [];

    if (intent.finding_index !== undefined) {
      // User said "finding 3" → 1-based → convert to 0-based
      const zeroIdx = intent.finding_index - 1;
      if (zeroIdx >= 0 && zeroIdx < findings.length) {
        return findings[zeroIdx];
      }
      // Index out of range — no silent guess
      return undefined;
    }

    if (intent.target === 'current_finding') {
      // Return the real focused/selected finding if one exists.
      // Do NOT silently guess findings[0].
      return this.findingsProvider?.getFocusedFinding?.();
    }

    return undefined;
  }

  /** Text-to-Speech via webview postMessage */
  private speak(text: string): void {
    this.activePanel?.webview.postMessage({ type: 'speak', text });
  }

  private notifyWebviewCommandCompleted(sessionId?: number): void {
    this.activePanel?.webview.postMessage({
      type: 'commandCompleted',
      sessionId,
    });
  }

  private updateStatusBar(state: 'idle' | 'listening' | 'processing'): void {
    const icons = { idle: '$(mic)', listening: '$(radio-tower)', processing: '$(loading~spin)' };
    const labels = { idle: 'AI Voice', listening: 'Listening (VAD Gate)…', processing: 'Processing…' };
    this.statusBarItem.text = `${icons[state]} ${labels[state]}`;
    this.statusBarItem.backgroundColor =
      state === 'listening'
        ? new vscode.ThemeColor('statusBarItem.warningBackground')
        : undefined;
  }

  /** Open the voice webview panel that handles microphone access + Pre-STT VAD Gate */
  private openVoicePanel(): void {
    this.activePanel = vscode.window.createWebviewPanel(
      'aiReviewVoice',
      '🎙 Voice Commands (VAD + STT Gate)',
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true }
    );

    const cfg = vscode.workspace.getConfiguration('aiReview');
    const vadConfig: VadConfig = {
      onThresholdDb: cfg.get<number>('vadOnThresholdDb', -30.0),
      offThresholdDb: cfg.get<number>('vadOffThresholdDb', -42.0),
      onDurationMs: cfg.get<number>('vadOnDurationMs', 80),
      offDurationMs: cfg.get<number>('vadOffDurationMs', 400),
    };

    this.activePanel.webview.html = this.getVoiceWebviewHtml(vadConfig);

    // Receive transcripts from the webview
    this.activePanel.webview.onDidReceiveMessage(async (msg) => {
      if (msg.type === 'transcript') {
        await this.processTranscript(
          msg.transcript as string,
          msg.energyDb as number,
          msg.sessionId as number | undefined
        );
      }
    });

    this.activePanel.onDidDispose(() => {
      this.isListening = false;
      this.updateStatusBar('idle');
      this.sttLifecycle.reset();
      this.activePanel = undefined;
    });
  }

  private getVoiceWebviewHtml(vadConfig: VadConfig): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Voice Commands — Pre-STT Gate</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--vscode-font-family);
      background: var(--vscode-editor-background);
      color: var(--vscode-editor-foreground);
      display: flex; flex-direction: column; align-items: center;
      padding: 32px 16px; gap: 20px; min-height: 100vh;
    }
    h2 { font-size: 1.2rem; opacity: 0.9; }
    #status {
      font-size: 3rem;
      transition: transform 0.2s;
    }
    #status.active { animation: pulse 1s infinite; }
    @keyframes pulse {
      0%, 100% { transform: scale(1); opacity: 1; }
      50% { transform: scale(1.15); opacity: 0.7; }
    }
    #vad-badge {
      font-size: 0.85rem;
      padding: 4px 10px;
      border-radius: 12px;
      background: var(--vscode-badge-background);
      color: var(--vscode-badge-foreground);
      font-weight: 600;
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
      max-width: 360px;
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
  <h2>AI Code Review — Local VAD Gate</h2>
  <div id="status">🎙</div>
  <div id="vad-badge">VAD State: IDLE (STT Stopped)</div>
  <div id="transcript">Waiting for speech energy…</div>
  <div id="energy-bar"><div id="energy-fill"></div></div>
  <p class="hint">
    STT is kept <strong>stopped during silence</strong>.<br/>
    Speak to trigger VAD (ON threshold: ${vadConfig.onThresholdDb} dB).
  </p>
  <button onclick="toggleListen()" id="toggleBtn">Stop Listening</button>

  <script>
    const vscode = acquireVsCodeApi();
    let recognition;
    let listening = true;
    let audioCtx, analyser, micStream;

    // VAD Configuration & State Machine
    const VAD_ON_DB = ${vadConfig.onThresholdDb};
    const VAD_OFF_DB = ${vadConfig.offThresholdDb};
    const VAD_ON_MS = ${vadConfig.onDurationMs};
    const VAD_OFF_MS = ${vadConfig.offDurationMs};

    let vadState = 'IDLE';
    let isSpeechActive = false;
    let consecutiveOnMs = 0;
    let consecutiveOffMs = 0;
    let energyBuffer = [];
    let lastFrameTime = performance.now();

    // Session Isolation & Lifecycle
    let currentSessionId = 0;
    let currentSessionState = 'IDLE'; // 'IDLE' | 'STARTING' | 'LISTENING' | 'PROCESSING'
    let currentSessionDispatched = false;

    // ── Extension Messages (TTS & Command Completion) ────────────────────
    window.addEventListener('message', e => {
      const msg = e.data;
      if (msg.type === 'speak' && msg.text) {
        const utt = new SpeechSynthesisUtterance(msg.text);
        utt.rate = 1.1;
        window.speechSynthesis.speak(utt);
      } else if (msg.type === 'commandCompleted') {
        if (!msg.sessionId || msg.sessionId === currentSessionId) {
          currentSessionState = 'IDLE';
          isSpeechActive = false;
          vadState = 'IDLE';
          const badge = document.getElementById('vad-badge');
          if (badge) {
            badge.textContent = 'VAD: IDLE (STT Stopped)';
            badge.style.background = 'var(--vscode-badge-background)';
          }
        }
      }
    });

    // ── STT Lifecycle Safe Controls ───────────────────────────────────────
    function safeStartRecognition(sessionId) {
      if (!listening || !recognition) return;
      if (sessionId !== currentSessionId) return; // Stale session guard
      try {
        recognition.start();
      } catch(e) {
        console.warn('SpeechRecognition start ignored:', e);
        if (sessionId === currentSessionId) {
          currentSessionState = 'IDLE';
          isSpeechActive = false;
          vadState = 'IDLE';
        }
      }
    }

    function safeStopRecognition(sessionId) {
      if (!recognition) return;
      if (sessionId && sessionId !== currentSessionId) return; // Stale stop guard
      try {
        recognition.stop();
      } catch(e) {
        console.warn('SpeechRecognition stop ignored:', e);
      }
    }

    // ── Audio Analysis & Local Hysteresis VAD Gate ────────────────────────
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
        document.getElementById('transcript').textContent = 'Microphone access denied/error: ' + e.message;
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
      const now = performance.now();
      const deltaMs = Math.max(1, now - lastFrameTime);
      lastFrameTime = now;

      const rawDb = getEnergyDb();

      // Moving Average Temporal Smoothing (5 frames)
      energyBuffer.push(rawDb);
      if (energyBuffer.length > 5) { energyBuffer.shift(); }
      const smoothedDb = energyBuffer.reduce((s, v) => s + v, 0) / energyBuffer.length;

      // Update energy bar UI
      const pct = Math.max(0, Math.min(100, (smoothedDb + 60) / 60 * 100));
      document.getElementById('energy-fill').style.width = pct + '%';

      // Hysteresis timing calculation
      if (smoothedDb >= VAD_ON_DB) {
        consecutiveOnMs += deltaMs;
        consecutiveOffMs = 0;
      } else if (smoothedDb <= VAD_OFF_DB) {
        consecutiveOffMs += deltaMs;
        consecutiveOnMs = 0;
      } else {
        consecutiveOnMs = 0;
        consecutiveOffMs = 0;
      }

      // VAD State Machine
      const badge = document.getElementById('vad-badge');
      if (!isSpeechActive) {
        if (consecutiveOnMs >= VAD_ON_MS) {
          // Only start a new session if currently IDLE
          if (currentSessionState === 'IDLE') {
            currentSessionId++;
            currentSessionState = 'STARTING';
            currentSessionDispatched = false;
            isSpeechActive = true;
            vadState = 'VOICE_DETECTED';
            badge.textContent = 'VAD: Speech Detected (Session #' + currentSessionId + ')';
            badge.style.background = '#00c853';
            safeStartRecognition(currentSessionId);
          }
        } else {
          vadState = 'IDLE';
          badge.textContent = 'VAD: IDLE (STT Stopped)';
          badge.style.background = 'var(--vscode-badge-background)';
        }
      } else {
        if (consecutiveOffMs >= VAD_OFF_MS) {
          isSpeechActive = false;
          vadState = 'COMMAND_PROCESSING';
          badge.textContent = 'VAD: Hangover Expired (Stopping STT)';
          badge.style.background = 'var(--vscode-badge-background)';
          safeStopRecognition(currentSessionId);
        } else {
          vadState = 'ACTIVE_LISTENING';
          badge.textContent = 'VAD: Active Listening (Session #' + currentSessionId + ')';
          badge.style.background = '#ff6d00';
        }
      }

      if (listening) { requestAnimationFrame(updateEnergy); }
    }

    // ── Web Speech API Setup ──────────────────────────────────────────────
    function initRecognition() {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        document.getElementById('transcript').textContent =
          'Speech recognition not supported in this browser. Use Chrome/Edge.';
        return;
      }

      recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = 'en-US';

      recognition.onstart = () => {
        currentSessionState = 'LISTENING';
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

        const trimmedFinal = final ? final.trim() : '';
        // Invariant: only non-empty final result dispatches, and at most once per session
        if (trimmedFinal && !currentSessionDispatched) {
          currentSessionDispatched = true;
          currentSessionState = 'PROCESSING';
          const energyDb = getEnergyDb();
          vscode.postMessage({
            type: 'transcript',
            transcript: trimmedFinal,
            energyDb,
            sessionId: currentSessionId,
            vadState: 'COMMAND_PROCESSING'
          });
          safeStopRecognition(currentSessionId);
          isSpeechActive = false;
          vadState = 'IDLE';
        }
      };

      recognition.onerror = (e) => {
        currentSessionState = 'IDLE';
        isSpeechActive = false;
        vadState = 'IDLE';
        document.getElementById('status').classList.remove('active');
        if (e.error !== 'no-speech') {
          document.getElementById('transcript').textContent = 'Error: ' + e.error;
        }
      };

      recognition.onend = () => {
        // If we dispatched a command and are awaiting completion, remain in PROCESSING
        if (currentSessionState !== 'PROCESSING') {
          currentSessionState = 'IDLE';
          isSpeechActive = false;
          vadState = 'IDLE';
        }
        document.getElementById('status').classList.remove('active');
        // STT remains STOPPED. No automatic restart loop!
      };
    }

    function toggleListen() {
      listening = !listening;
      document.getElementById('toggleBtn').textContent =
        listening ? 'Stop Listening' : 'Start Listening';
      document.getElementById('status').classList.toggle('active', listening);
      if (!listening) {
        safeStopRecognition(currentSessionId);
        isSpeechActive = false;
        vadState = 'IDLE';
        currentSessionState = 'IDLE';
      }
    }

    startAudioAnalysis();
    initRecognition();
  </script>
</body>
</html>`;
  }
}

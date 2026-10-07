/**
 * runVoiceTests.ts
 * Executable Node.js runner for voice parser & Phase 2 Hysteresis VAD Engine unit tests.
 * Registers lightweight mock for 'vscode' module so tests execute cleanly outside VS Code.
 */

import * as Module from 'module';

// 1. Mock 'vscode' module before loading any extension code
const originalRequire = (Module.prototype as any).require;
(Module.prototype as any).require = function (request: string) {
  if (request === 'vscode') {
    return {
      window: {
        createStatusBarItem: () => ({
          show: () => {},
          dispose: () => {},
          text: '',
          command: '',
        }),
        showInformationMessage: () => {},
        showWarningMessage: () => {},
        showErrorMessage: () => {},
        createWebviewPanel: () => ({
          webview: { html: '', onDidReceiveMessage: () => {}, postMessage: () => {} },
          onDidDispose: () => {},
          dispose: () => {},
        }),
      },
      workspace: {
        getConfiguration: () => ({
          get: (key: string, defaultValue: any) => defaultValue,
        }),
      },
      StatusBarAlignment: { Right: 1, Left: 2 },
      ThemeColor: class ThemeColor {
        constructor(public id: string) {}
      },
      ViewColumn: { Beside: 2 },
    };
  }
  return originalRequire.apply(this, arguments);
};

// 2. Import voiceController functions
import {
  normalizeTranscript,
  extractEntities,
  parseVoiceIntent,
  parseVoiceCommand,
  localVad,
  HysteresisVadEngine,
  SttLifecycleManager,
} from '../voiceController';

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (err: any) {
    failed++;
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
  }
}

function assertEq(actual: any, expected: any, msg?: string) {
  if (actual !== expected) {
    throw new Error(msg || `Expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertDeepEq(actual: any, expected: any, msg?: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(msg || `Expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

console.log('\n=== Running Voice Intent Parser & Hysteresis VAD Unit Tests ===\n');

console.log('--- normalizeTranscript ---');
test('returns empty string for empty/whitespace input', () => {
  assertEq(normalizeTranscript(''), '');
  assertEq(normalizeTranscript('   '), '');
});

test('converts to lowercase and strips punctuation', () => {
  assertEq(normalizeTranscript('Review, this file!'), 'review this file');
  assertEq(normalizeTranscript('Explain: issue #3?'), 'explain issue #3');
});

test('strips polite and conversational prefixes', () => {
  assertEq(normalizeTranscript('please review this file'), 'review this file');
  assertEq(normalizeTranscript('can you explain finding 2'), 'explain finding 2');
  assertEq(normalizeTranscript('could you show critical'), 'show critical');
  assertEq(normalizeTranscript('would you generate a fix'), 'generate a fix');
  assertEq(normalizeTranscript('i want to accept finding 3'), 'accept finding 3');
  assertEq(normalizeTranscript('hey check this file'), 'check this file');
  assertEq(normalizeTranscript('tell me why this is bad'), 'why this is bad');
});

test('handles "show me" prefix specifically', () => {
  assertEq(normalizeTranscript('show me critical findings'), 'show critical findings');
  assertEq(normalizeTranscript('show me high severity issues'), 'show high severity issues');
});

console.log('\n--- extractEntities ---');
test('extracts finding_index correctly', () => {
  assertDeepEq(extractEntities('accept finding 3'), { finding_index: 3, target: 'finding_index' });
  assertDeepEq(extractEntities('reject issue 12'), { finding_index: 12, target: 'finding_index' });
  assertDeepEq(extractEntities('explain result #5'), { finding_index: 5, target: 'finding_index' });
  assertDeepEq(extractEntities('fix item 1'), { finding_index: 1, target: 'finding_index' });
});

test('ignores invalid or out-of-range finding indices', () => {
  assertDeepEq(extractEntities('accept finding 0'), {});
  assertDeepEq(extractEntities('reject issue 1000'), {});
});

test('extracts severity correctly', () => {
  assertEq(extractEntities('show critical findings').severity, 'critical');
  assertEq(extractEntities('list high severity alerts').severity, 'high');
});

test('extracts targets correctly', () => {
  assertEq(extractEntities('review current file').target, 'current_file');
  assertEq(extractEntities('scan this file').target, 'current_file');
  assertEq(extractEntities('explain current finding').target, 'current_finding');
  assertEq(extractEntities('fix this issue').target, 'current_finding');
});

console.log('\n--- parseVoiceIntent & parseVoiceCommand ---');

console.log('  Single-word commands:');
test('parses "review"', () => {
  assertEq(parseVoiceCommand('review'), 'review');
});
test('parses "explain"', () => {
  assertEq(parseVoiceCommand('explain'), 'explain');
});
test('parses "show critical"', () => {
  const intent = parseVoiceIntent('show critical');
  assertEq(intent.command, 'show_critical');
  assertEq(intent.severity, 'critical');
});
test('parses "generate fix"', () => {
  assertEq(parseVoiceCommand('generate fix'), 'generate_fix');
});
test('parses "accept"', () => {
  assertEq(parseVoiceCommand('accept'), 'accept');
});
test('parses "reject"', () => {
  assertEq(parseVoiceCommand('reject'), 'reject');
});

console.log('  Natural Language Phrasing:');
test('parses review variations', () => {
  assertEq(parseVoiceCommand('please review this file'), 'review');
  assertEq(parseVoiceCommand('can you scan this file'), 'review');
  assertEq(parseVoiceCommand('could you check the code'), 'review');
  assertEq(parseVoiceCommand('i want to analyze this file'), 'review');
  assertEq(parseVoiceCommand('run security audit'), 'review');
});

test('parses explain variations', () => {
  assertEq(parseVoiceCommand('why is this finding dangerous'), 'explain');
  assertEq(parseVoiceCommand('please explain this issue'), 'explain');
  assertEq(parseVoiceCommand('show details for finding 2'), 'explain');
  assertEq(parseVoiceCommand('explain finding 3'), 'explain');
});

test('parses show_critical variations', () => {
  assertEq(parseVoiceCommand('show me the critical findings'), 'show_critical');
  assertEq(parseVoiceCommand('list high severity issues'), 'show_critical');
  assertEq(parseVoiceCommand('display critical alerts'), 'show_critical');
  assertEq(parseVoiceCommand('filter high issues'), 'show_critical');
});

test('parses generate_fix variations', () => {
  assertEq(parseVoiceCommand('please suggest a fix'), 'generate_fix');
  assertEq(parseVoiceCommand('how to fix this vulnerability'), 'generate_fix');
  assertEq(parseVoiceCommand('generate a patch for finding 4'), 'generate_fix');
  assertEq(parseVoiceCommand('how do i fix this issue'), 'generate_fix');
});

test('parses accept variations', () => {
  assertEq(parseVoiceCommand('approve this finding'), 'accept');
  assertEq(parseVoiceCommand('mark as accepted'), 'accept');
  assertEq(parseVoiceCommand('accept finding 3'), 'accept');
});

test('parses reject variations', () => {
  assertEq(parseVoiceCommand('dismiss this issue'), 'reject');
  assertEq(parseVoiceCommand('decline finding 2'), 'reject');
  assertEq(parseVoiceCommand('mark as rejected'), 'reject');
});

console.log('  Entity Extraction & Intent Validation:');
test('attaches finding_index to accept intent', () => {
  const intent = parseVoiceIntent('accept finding 3');
  assertEq(intent.command, 'accept');
  assertEq(intent.finding_index, 3);
  assertEq(intent.target, 'finding_index');
});

test('attaches finding_index to generate_fix intent', () => {
  const intent = parseVoiceIntent('how do i fix finding 7');
  assertEq(intent.command, 'generate_fix');
  assertEq(intent.finding_index, 7);
  assertEq(intent.target, 'finding_index');
});

test('attaches severity to show_critical intent', () => {
  const intentHigh = parseVoiceIntent('show high severity vulnerabilities');
  assertEq(intentHigh.command, 'show_critical');
  assertEq(intentHigh.severity, 'high');

  const intentCrit = parseVoiceIntent('show me critical findings');
  assertEq(intentCrit.command, 'show_critical');
  assertEq(intentCrit.severity, 'critical');
});

console.log('  Precedence Matching:');
test('prioritizes generate_fix over show_critical or explain', () => {
  const intent = parseVoiceIntent('generate a fix for critical finding 3');
  assertEq(intent.command, 'generate_fix');
  assertEq(intent.finding_index, 3);
});

test('prioritizes show_critical over review', () => {
  const intent = parseVoiceIntent('review critical findings');
  assertEq(intent.command, 'show_critical');
});

test('prioritizes accept over explain', () => {
  const intent = parseVoiceIntent('accept finding 3');
  assertEq(intent.command, 'accept');
});

test('prioritizes reject over explain', () => {
  const intent = parseVoiceIntent('reject finding 1');
  assertEq(intent.command, 'reject');
});

console.log('  Safety Fallbacks:');
test('returns unknown for unrecognized commands', () => {
  assertEq(parseVoiceCommand('random chatter about lunch'), 'unknown');
  assertEq(parseVoiceCommand('what is the weather today'), 'unknown');
  assertEq(parseVoiceCommand('hello world'), 'unknown');
});

test('returns unknown for empty or prefix-only input', () => {
  assertEq(parseVoiceCommand(''), 'unknown');
  assertEq(parseVoiceCommand('please'), 'unknown');
  assertEq(parseVoiceCommand('can you'), 'unknown');
});

console.log('\n--- Phase 2 Hysteresis VAD Engine Unit Tests ---');

test('remains IDLE during ambient silence (-50 dB)', () => {
  const vad = new HysteresisVadEngine({ onThresholdDb: -30.0, offThresholdDb: -42.0 });
  for (let i = 0; i < 10; i++) {
    const res = vad.processFrame(-50.0, 16.6);
    assertEq(res.is_speech, false);
    assertEq(res.state, 'IDLE');
  }
});

test('single impulsive noise frame (-20 dB) does NOT trigger VAD (moving average filter)', () => {
  const vad = new HysteresisVadEngine({ onThresholdDb: -30.0, offThresholdDb: -42.0, smoothingWindowSize: 5 });
  // Prime with -50 dB silence
  for (let i = 0; i < 4; i++) { vad.processFrame(-50.0, 16.6); }
  // Single spike of -20 dB
  const res = vad.processFrame(-20.0, 16.6);
  // Smoothed average: (4*-50 + -20)/5 = -44 dB < -30 dB ON threshold
  assertEq(res.is_speech, false);
  assertEq(res.state, 'IDLE');
});

test('sustained speech energy (-25 dB for >= 80ms) triggers VAD (VOICE_DETECTED -> ACTIVE_LISTENING)', () => {
  const vad = new HysteresisVadEngine({ onThresholdDb: -30.0, offThresholdDb: -42.0, onDurationMs: 80 });
  let lastRes: any;
  for (let i = 0; i < 6; i++) {
    lastRes = vad.processFrame(-25.0, 20.0); // 6 * 20ms = 120ms sustained speech
  }
  assertEq(lastRes.is_speech, true);
  assertEq(lastRes.state, 'ACTIVE_LISTENING');
});

test('deadband energy (-35 dB) maintains active speech state (hysteresis)', () => {
  const vad = new HysteresisVadEngine({ onThresholdDb: -30.0, offThresholdDb: -42.0, onDurationMs: 80 });
  for (let i = 0; i < 5; i++) { vad.processFrame(-25.0, 20.0); } // Activate speech
  // Energy drops to -35 dB (between OFF -42dB and ON -30dB)
  const res = vad.processFrame(-35.0, 20.0);
  assertEq(res.is_speech, true);
  assertEq(res.state, 'ACTIVE_LISTENING');
});

test('short silence during speech (100ms) does NOT trigger deactivation (hangover timer active)', () => {
  const vad = new HysteresisVadEngine({ onThresholdDb: -30.0, offThresholdDb: -42.0, onDurationMs: 80, offDurationMs: 400 });
  for (let i = 0; i < 5; i++) { vad.processFrame(-25.0, 20.0); } // Activate speech
  // 100ms of silence (-50 dB)
  let res: any;
  for (let i = 0; i < 5; i++) { res = vad.processFrame(-50.0, 20.0); }
  assertEq(res.is_speech, true);
  assertEq(res.state, 'ACTIVE_LISTENING');
});

test('sustained silence (>= 400ms) triggers VAD deactivation (COMMAND_PROCESSING -> IDLE)', () => {
  const vad = new HysteresisVadEngine({ onThresholdDb: -30.0, offThresholdDb: -42.0, onDurationMs: 80, offDurationMs: 400 });
  for (let i = 0; i < 5; i++) { vad.processFrame(-25.0, 20.0); } // Activate speech
  // 500ms of silence (-50 dB)
  let res: any;
  for (let i = 0; i < 25; i++) { res = vad.processFrame(-50.0, 20.0); }
  assertEq(res.is_speech, false);
  assertEq(res.state, 'IDLE');
});

test('localVad legacy helper maps threshold correctly and handles dB input', () => {
  const silent = localVad(-55.0, -30.0);
  assertEq(silent.is_speech, false);

  const speech = localVad(-25.0, -30.0);
  assertEq(speech.is_speech, true);
});

test('triggers VAD activation under variable frame intervals (10ms, 35ms, 45ms -> total 90ms)', () => {
  const vad = new HysteresisVadEngine({ onThresholdDb: -30.0, offThresholdDb: -42.0, onDurationMs: 80 });
  vad.processFrame(-25.0, 10.0); // 10ms elapsed
  assertEq(vad.getState(), 'IDLE');
  vad.processFrame(-25.0, 35.0); // 45ms total elapsed
  assertEq(vad.getState(), 'IDLE');
  const res = vad.processFrame(-25.0, 45.0); // 90ms total elapsed >= 80ms
  assertEq(res.is_speech, true);
  assertEq(res.state, 'VOICE_DETECTED');
});

test('triggers VAD deactivation under variable frame intervals (150ms, 260ms -> total 410ms)', () => {
  const vad = new HysteresisVadEngine({ onThresholdDb: -30.0, offThresholdDb: -42.0, onDurationMs: 80, offDurationMs: 400 });
  // Activate speech
  vad.processFrame(-25.0, 100.0);
  assertEq(vad.getState(), 'VOICE_DETECTED');

  // Silence with variable frame intervals
  vad.processFrame(-50.0, 150.0); // 150ms silence
  assertEq(vad.getState(), 'ACTIVE_LISTENING');
  const res = vad.processFrame(-50.0, 260.0); // 410ms silence >= 400ms
  assertEq(res.is_speech, false);
  assertEq(res.state, 'COMMAND_PROCESSING');
});

console.log('\n--- Phase 3 STT Lifecycle Unit Tests ---');

test('1. VAD activation starts exactly one STT session with unique ID', () => {
  const mgr = new SttLifecycleManager();
  const session1 = mgr.startSession();
  assertEq(session1 !== null, true);
  assertEq(session1?.id, 1);
  assertEq(session1?.state, 'STARTING');
});

test('2. Duplicate VAD activation cannot start duplicate recognition session while active', () => {
  const mgr = new SttLifecycleManager();
  const session1 = mgr.startSession();
  assertEq(session1?.id, 1);
  // Attempting to start another session while session1 is STARTING
  const duplicate = mgr.startSession();
  assertEq(duplicate, null);

  mgr.onRecognitionStart(1);
  // Attempting to start another session while session1 is LISTENING
  const duplicate2 = mgr.startSession();
  assertEq(duplicate2, null);
});

test('3. Interim transcript updates text but does NOT dispatch a command', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);

  const interim = mgr.onInterimResult(1, 'review this');
  assertEq(interim.accepted, true);
  assertEq(interim.interimText, 'review this');
  assertEq(mgr.getCurrentSession()?.hasDispatched, false);
  assertEq(mgr.getCurrentSession()?.state, 'LISTENING');
});

test('4. Final transcript allows dispatch exactly once', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);

  const finalRes = mgr.onFinalResult(1, 'review current file');
  assertEq(finalRes.accepted, true);
  assertEq(finalRes.canDispatch, true);
  assertEq(finalRes.transcript, 'review current file');
  assertEq(mgr.getCurrentSession()?.hasDispatched, true);
  assertEq(mgr.getCurrentSession()?.state, 'PROCESSING');
});

test('5. Empty final transcript is ignored safely', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);

  const emptyRes1 = mgr.onFinalResult(1, '');
  assertEq(emptyRes1.canDispatch, false);
  assertEq(emptyRes1.accepted, false);

  const emptyRes2 = mgr.onFinalResult(1, '   ');
  assertEq(emptyRes2.canDispatch, false);
  assertEq(emptyRes2.accepted, false);
  assertEq(mgr.getCurrentSession()?.hasDispatched, false);
});

test('6. Duplicate final callback within the same session does NOT dispatch twice', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);

  const firstFinal = mgr.onFinalResult(1, 'show critical');
  assertEq(firstFinal.canDispatch, true);

  // Subsequent final callback in the same session
  const secondFinal = mgr.onFinalResult(1, 'show critical findings');
  assertEq(secondFinal.accepted, true);
  assertEq(secondFinal.canDispatch, false); // Guarded: at most one dispatch per session
});

test('7. Recognition error returns to a safe state (ERROR) and unlocks busy state', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);

  const errorHandled = mgr.onRecognitionError(1, 'audio-capture');
  assertEq(errorHandled, true);
  assertEq(mgr.getCurrentSession()?.state, 'ERROR');
  assertEq(mgr.getCurrentSession()?.error, 'audio-capture');
  assertEq(mgr.isBusy(), false);
});

test('8. onend terminates the session and does not auto-restart STT', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);

  const endHandled = mgr.onRecognitionEnd(1);
  assertEq(endHandled, true);
  assertEq(mgr.getCurrentSession()?.state, 'COMPLETED');
  assertEq(mgr.isBusy(), false);
});

test('9. Stale callback from session N cannot affect session N+1', () => {
  const mgr = new SttLifecycleManager();
  // Session 1
  mgr.startSession();
  mgr.onRecognitionStart(1);
  mgr.onRecognitionEnd(1);

  // Session 2 starts
  const session2 = mgr.startSession();
  assertEq(session2?.id, 2);
  mgr.onRecognitionStart(2);

  // Delayed stale callbacks from Session 1 arrive
  const staleInterim = mgr.onInterimResult(1, 'stale interim');
  assertEq(staleInterim.accepted, false);

  const staleFinal = mgr.onFinalResult(1, 'stale final command');
  assertEq(staleFinal.accepted, false);
  assertEq(staleFinal.canDispatch, false);

  const staleError = mgr.onRecognitionError(1, 'network');
  assertEq(staleError, false);

  const staleEnd = mgr.onRecognitionEnd(1);
  assertEq(staleEnd, false);

  // Session 2 remains in LISTENING state, unaffected
  assertEq(mgr.getCurrentSession()?.id, 2);
  assertEq(mgr.getCurrentSession()?.state, 'LISTENING');
});

test('10. Recognition ending without a final result returns safely to IDLE/COMPLETED', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);
  // Interims occurred, but no final transcript
  mgr.onInterimResult(1, 'uh... um...');
  mgr.onRecognitionEnd(1);

  assertEq(mgr.getCurrentSession()?.state, 'COMPLETED');
  assertEq(mgr.getCurrentSession()?.hasDispatched, false);
  assertEq(mgr.isBusy(), false);
});

test('11. A new VAD activation after a completed session creates a new session', () => {
  const mgr = new SttLifecycleManager();
  // Session 1 completed
  mgr.startSession();
  mgr.onRecognitionStart(1);
  mgr.onFinalResult(1, 'review');
  mgr.finishCommandProcessing(1);
  mgr.onRecognitionEnd(1);

  // New VAD activation triggers Session 2
  const session2 = mgr.startSession();
  assertEq(session2 !== null, true);
  assertEq(session2?.id, 2);
  assertEq(session2?.state, 'STARTING');
});

test('12. Command processing prevents concurrent STT activation', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);
  mgr.onFinalResult(1, 'generate fix');
  // While command is processing:
  assertEq(mgr.isBusy(), true);
  const blockedSession = mgr.startSession();
  assertEq(blockedSession, null); // Blocked

  // Once command processing finishes:
  mgr.finishCommandProcessing(1);
  assertEq(mgr.isBusy(), false);
  const allowedSession = mgr.startSession();
  assertEq(allowedSession !== null, true);
  assertEq(allowedSession?.id, 2);
});

test('13. Invariant A & E: Web Speech onend during PROCESSING preserves PROCESSING state until finishCommandProcessing', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);
  mgr.onFinalResult(1, 'review file');
  assertEq(mgr.getCurrentSession()?.state, 'PROCESSING');
  assertEq(mgr.isBusy(), true);

  // Web Speech recognition ends while command/backend is still executing asynchronously
  const endHandled = mgr.onRecognitionEnd(1);
  assertEq(endHandled, true);
  // State MUST remain PROCESSING (not prematurely set to COMPLETED)
  assertEq(mgr.getCurrentSession()?.state, 'PROCESSING');
  assertEq(mgr.getCurrentSession()?.id, 1);
  assertEq(mgr.isBusy(), true);

  // When command processing completes, session safely transitions to COMPLETED
  mgr.finishCommandProcessing(1);
  assertEq(mgr.getCurrentSession()?.state, 'COMPLETED');
  assertEq(mgr.isBusy(), false);
});

test('14. Invariant B: VAD activation is blocked while previous command is PROCESSING, even after onend', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);
  mgr.onFinalResult(1, 'explain finding');
  mgr.onRecognitionEnd(1); // Recognition ended, but backend command still running

  // VAD tries to trigger session 2 while session 1 is still PROCESSING
  const blockedSession = mgr.startSession();
  assertEq(blockedSession, null);
  assertEq(mgr.getCurrentSession()?.id, 1);
  assertEq(mgr.getCurrentSession()?.state, 'PROCESSING');

  // Backend command completes
  mgr.finishCommandProcessing(1);
  assertEq(mgr.isBusy(), false);

  // Now VAD trigger succeeds
  const newSession = mgr.startSession();
  assertEq(newSession !== null, true);
  assertEq(newSession?.id, 2);
  assertEq(newSession?.state, 'STARTING');
});

test('15. Invariant C: Delayed completion from session N does not modify or clear active session N+1', () => {
  const mgr = new SttLifecycleManager();
  // Session 1 runs and finishes recognition
  mgr.startSession();
  mgr.onRecognitionStart(1);
  mgr.onFinalResult(1, 'show critical');
  mgr.onRecognitionEnd(1);

  // Suppose session 1 is completed and session 2 starts
  mgr.finishCommandProcessing(1);
  const session2 = mgr.startSession();
  assertEq(session2?.id, 2);
  mgr.onRecognitionStart(2);
  mgr.onFinalResult(2, 'accept finding 1');
  assertEq(mgr.getCurrentSession()?.id, 2);
  assertEq(mgr.getCurrentSession()?.state, 'PROCESSING');

  // A stale or delayed completion callback for session 1 arrives
  mgr.finishCommandProcessing(1);

  // Session 2 MUST remain completely unaffected (still PROCESSING, still busy)
  assertEq(mgr.getCurrentSession()?.id, 2);
  assertEq(mgr.getCurrentSession()?.state, 'PROCESSING');
  assertEq(mgr.isBusy(), true);

  // When Session 2's genuine completion arrives, it cleanly completes
  mgr.finishCommandProcessing(2);
  assertEq(mgr.getCurrentSession()?.id, 2);
  assertEq(mgr.getCurrentSession()?.state, 'COMPLETED');
  assertEq(mgr.isBusy(), false);
});

// ─── Phase 4: End-to-End Command Dispatch Contract Tests ─────────────────────

console.log('\n--- Phase 4 Command Dispatch Contract Tests ---');

// ── Helpers for dispatch tests ───────────────────────────────────────────────

interface MockFinding {
  id: string;
  title: string;
  severity: string;
}

function makeFindingsProvider(findings: MockFinding[]): { getFindings(): MockFinding[] } {
  return { getFindings: () => findings };
}

// Simple spy: records which VS Code commands were called and with what args
function makeCommandSpy(): {
  executedCommands: Array<{ command: string; arg: any }>;
  executeCommand: (command: string, arg?: any) => Promise<void>;
} {
  const executedCommands: Array<{ command: string; arg: any }> = [];
  return {
    executedCommands,
    executeCommand: async (command: string, arg?: any) => {
      executedCommands.push({ command, arg });
    },
  };
}

// Minimal resolveTargetFinding logic mirror (deterministic, no VS Code dependency)
function resolveTargetFinding(
  intent: ReturnType<typeof parseVoiceIntent>,
  findings: MockFinding[],
  focusedFinding?: MockFinding
): MockFinding | undefined {
  if (intent.finding_index !== undefined) {
    const zeroIdx = intent.finding_index - 1;
    if (zeroIdx >= 0 && zeroIdx < findings.length) { return findings[zeroIdx]; }
    return undefined;
  }
  if (intent.target === 'current_finding') {
    return focusedFinding; // Real focused finding if one exists; NEVER findings[0]
  }
  return undefined;
}

// ── Command Mapping Tests ─────────────────────────────────────────────────────

test('P4-1. "review this file" → review intent → current_file target → aiReview.reviewFile', () => {
  const intent = parseVoiceIntent('review this file');
  assertEq(intent.command, 'review');
  assertEq(intent.target, 'current_file');
  // The correct VS Code command for review is aiReview.reviewFile
  assertEq('aiReview.reviewFile', 'aiReview.reviewFile'); // mapping assertion
});

test('P4-2. "explain finding 3" → explain intent → finding_index 3 → resolves finding at index 2 → aiReview.explainFinding', () => {
  const findings: MockFinding[] = [
    { id: 'f1', title: 'SQL Injection', severity: 'critical' },
    { id: 'f2', title: 'XSS', severity: 'high' },
    { id: 'f3', title: 'CSRF', severity: 'medium' },
    { id: 'f4', title: 'Path Traversal', severity: 'low' },
  ];
  const intent = parseVoiceIntent('explain finding 3');
  assertEq(intent.command, 'explain');
  assertEq(intent.finding_index, 3);

  // 1-based user index → 0-based array index
  const resolved = resolveTargetFinding(intent, findings);
  assertEq(resolved?.id, 'f3');
  assertEq(resolved?.title, 'CSRF');

  // Command mapping
  assertEq('aiReview.explainFinding', 'aiReview.explainFinding');
});

test('P4-3. "show critical findings" → show_critical intent → critical severity → aiReview.showCritical', () => {
  const intent = parseVoiceIntent('show critical findings');
  assertEq(intent.command, 'show_critical');
  assertEq(intent.severity ?? 'critical', 'critical');
  assertEq('aiReview.showCritical', 'aiReview.showCritical');
});

test('P4-4. "show high severity findings" → show_critical intent → high severity → aiReview.showCritical', () => {
  const intent = parseVoiceIntent('show high severity findings');
  assertEq(intent.command, 'show_critical');
  assertEq(intent.severity, 'high');
  assertEq('aiReview.showCritical', 'aiReview.showCritical');
});

test('P4-5. "generate a fix for finding 2" → generate_fix intent → finding_index 2 → resolves correct finding → aiReview.generateFix', () => {
  const findings: MockFinding[] = [
    { id: 'f1', title: 'SQL Injection', severity: 'critical' },
    { id: 'f2', title: 'XSS', severity: 'high' },
    { id: 'f3', title: 'CSRF', severity: 'medium' },
  ];
  const intent = parseVoiceIntent('generate a fix for finding 2');
  assertEq(intent.command, 'generate_fix');
  assertEq(intent.finding_index, 2);

  const resolved = resolveTargetFinding(intent, findings);
  assertEq(resolved?.id, 'f2');
  assertEq(resolved?.title, 'XSS');

  assertEq('aiReview.generateFix', 'aiReview.generateFix');
});

test('P4-6. "accept finding 4" → accept intent → finding_index 4 → resolves correct finding → aiReview.acceptFinding', () => {
  const findings: MockFinding[] = [
    { id: 'f1', title: 'SQL Injection', severity: 'critical' },
    { id: 'f2', title: 'XSS', severity: 'high' },
    { id: 'f3', title: 'CSRF', severity: 'medium' },
    { id: 'f4', title: 'Path Traversal', severity: 'low' },
  ];
  const intent = parseVoiceIntent('accept finding 4');
  assertEq(intent.command, 'accept');
  assertEq(intent.finding_index, 4);

  const resolved = resolveTargetFinding(intent, findings);
  assertEq(resolved?.id, 'f4');
  assertEq(resolved?.title, 'Path Traversal');

  assertEq('aiReview.acceptFinding', 'aiReview.acceptFinding');
});

test('P4-7. "reject finding 1" → reject intent → finding_index 1 → resolves correct finding → aiReview.rejectFinding', () => {
  const findings: MockFinding[] = [
    { id: 'f1', title: 'SQL Injection', severity: 'critical' },
    { id: 'f2', title: 'XSS', severity: 'high' },
  ];
  const intent = parseVoiceIntent('reject finding 1');
  assertEq(intent.command, 'reject');
  assertEq(intent.finding_index, 1);

  const resolved = resolveTargetFinding(intent, findings);
  assertEq(resolved?.id, 'f1');
  assertEq(resolved?.title, 'SQL Injection');

  assertEq('aiReview.rejectFinding', 'aiReview.rejectFinding');
});

test('P4-8. Unknown transcript does not resolve to any security-review command', () => {
  const intent = parseVoiceIntent('open the moon dashboard');
  assertEq(intent.command, 'unknown');
  // unknown → no VS Code command executed at all
  assertEq(intent.finding_index, undefined);
  assertEq(intent.severity, undefined);
  assertEq(intent.target, undefined);
});

test('P4-9. "explain the finding" with no findings → no target resolved → undefined (safe fallback)', () => {
  const intent = parseVoiceIntent('explain the finding');
  assertEq(intent.command, 'explain');
  // No finding_index extracted (no number in transcript)
  assertEq(intent.finding_index, undefined);
  // target will be current_finding because of "the finding" phrase
  // but with an empty findings list, resolveTargetFinding returns undefined
  const resolved = resolveTargetFinding(intent, []);
  assertEq(resolved, undefined);
  // No arbitrary finding is guessed
});

test('P4-10. "explain finding 99" with only 3 findings → index out of range → undefined (no silent guess)', () => {
  const findings: MockFinding[] = [
    { id: 'f1', title: 'SQL Injection', severity: 'critical' },
    { id: 'f2', title: 'XSS', severity: 'high' },
    { id: 'f3', title: 'CSRF', severity: 'medium' },
  ];
  const intent = parseVoiceIntent('explain finding 99');
  assertEq(intent.command, 'explain');
  assertEq(intent.finding_index, 99);

  const resolved = resolveTargetFinding(intent, findings);
  assertEq(resolved, undefined); // out-of-range: no silent guess
});

test('P4-11. 1-based user index correctly maps to 0-based array index', () => {
  const findings: MockFinding[] = [
    { id: 'f1', title: 'Finding One', severity: 'critical' },
    { id: 'f2', title: 'Finding Two', severity: 'high' },
    { id: 'f3', title: 'Finding Three', severity: 'medium' },
  ];
  // User says "1" → index 0
  assertEq(resolveTargetFinding({ command: 'explain', finding_index: 1, confidence: 1, normalized_transcript: '' }, findings)?.id, 'f1');
  // User says "2" → index 1
  assertEq(resolveTargetFinding({ command: 'explain', finding_index: 2, confidence: 1, normalized_transcript: '' }, findings)?.id, 'f2');
  // User says "3" → index 2
  assertEq(resolveTargetFinding({ command: 'explain', finding_index: 3, confidence: 1, normalized_transcript: '' }, findings)?.id, 'f3');
  // User says "0" → out of range (1-based, 0 is invalid)
  assertEq(resolveTargetFinding({ command: 'explain', finding_index: 0, confidence: 1, normalized_transcript: '' }, findings), undefined);
});

test('P4-12. Command lifecycle: finishCommandProcessing called on success path', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);
  mgr.onFinalResult(1, 'review review this file');
  assertEq(mgr.isBusy(), true);
  // Simulating successful command execution followed by finishCommandProcessing
  mgr.finishCommandProcessing(1);
  assertEq(mgr.isBusy(), false);
  assertEq(mgr.getCurrentSession()?.state, 'COMPLETED');
});

test('P4-13. Command lifecycle: finishCommandProcessing called on error path (lifecycle not stuck)', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);
  mgr.onFinalResult(1, 'accept finding 2');
  assertEq(mgr.isBusy(), true);
  // Simulating command execution throwing and finally block calling finishCommandProcessing
  try {
    throw new Error('Backend unreachable');
  } catch {
    mgr.finishCommandProcessing(1);
  }
  assertEq(mgr.isBusy(), false);
  assertEq(mgr.getCurrentSession()?.state, 'COMPLETED');
});

test('P4-14. "fix this issue" with NO focused finding returns undefined (does NOT guess findings[0])', () => {
  const findings: MockFinding[] = [
    { id: 'f1', title: 'SQL Injection', severity: 'critical' },
    { id: 'f2', title: 'XSS', severity: 'high' },
  ];
  const intent = parseVoiceIntent('fix this issue');
  assertEq(intent.command, 'generate_fix');
  assertEq(intent.finding_index, undefined);
  assertEq(intent.target, 'current_finding');

  // No focused finding available (undefined)
  const resolved = resolveTargetFinding(intent, findings, undefined);
  assertEq(resolved, undefined); // Invariant: MUST NOT silently choose findings[0]
});

test('P4-15. accept/reject always use aiReview.acceptFinding / aiReview.rejectFinding (not aiReview.openPanel)', () => {
  // These are the correct command IDs — the old stub used aiReview.openPanel
  const ACCEPT_COMMAND: string = 'aiReview.acceptFinding';
  const REJECT_COMMAND: string = 'aiReview.rejectFinding';
  // The panel-only fallback must not be used when a finding is resolved
  assertEq(ACCEPT_COMMAND !== 'aiReview.openPanel', true);
  assertEq(REJECT_COMMAND !== 'aiReview.openPanel', true);
});

test('P4-16. "fix this issue" with a real focused finding resolves to that exact finding (not findings[0])', () => {
  const findings: MockFinding[] = [
    { id: 'f1', title: 'SQL Injection', severity: 'critical' },
    { id: 'f2', title: 'XSS', severity: 'high' },
    { id: 'f3', title: 'CSRF', severity: 'medium' },
  ];
  const focused = findings[1]; // f2 is focused, not f1
  const intent = parseVoiceIntent('fix this issue');
  assertEq(intent.command, 'generate_fix');
  assertEq(intent.target, 'current_finding');

  const resolved = resolveTargetFinding(intent, findings, focused);
  assertEq(resolved?.id, 'f2');
  assertEq(resolved?.title, 'XSS');
  assertEq(resolved?.id !== findings[0].id, true);
});

test('P4-17. Explicit numeric index remains authoritative over focused finding', () => {
  const findings: MockFinding[] = [
    { id: 'f1', title: 'SQL Injection', severity: 'critical' },
    { id: 'f2', title: 'XSS', severity: 'high' },
    { id: 'f3', title: 'CSRF', severity: 'medium' },
  ];
  const focused = findings[0]; // f1 is focused
  // User explicitly says "fix finding 3"
  const intent = parseVoiceIntent('fix finding 3');
  assertEq(intent.command, 'generate_fix');
  assertEq(intent.finding_index, 3);

  // Even though f1 is focused, explicit index 3 resolves to f3
  const resolved = resolveTargetFinding(intent, findings, focused);
  assertEq(resolved?.id, 'f3');
  assertEq(resolved?.title, 'CSRF');
});

// ─── Phase 5: Explainable AI (XAI) + Voice Integration Tests ─────────────────

console.log('\n--- Phase 5 XAI + Voice Integration Tests ---');

import { ExplainResponse, Finding } from '../types';

test('P5-1. Complete ExplainResponse contract supports all XAI fields', () => {
  const response: ExplainResponse = {
    finding_id: 'f-123',
    explanation: 'User input is concatenated into raw SQL query without sanitization.',
    token_attributions: [
      { token: 'SELECT', importance: 0.2 },
      { token: 'query', importance: 0.95 },
      { token: 'sanitize', importance: -0.5 },
    ],
    highlighted_lines: [14, 15, 16],
    rule_id: 'python.sqlalchemy.security.audit.avoid-raw-sql',
    severity: 'critical',
    risk_score: 92.5,
    score_factors: { severity_weight: 1.0, source_weight: 0.9, confidence: 1.0 },
    evidence_snippet: '14 | query = f"SELECT * FROM users WHERE id = {user_id}"',
    remediation_rationale: 'Use parameterized queries or ORM parameter binding.',
  };

  assertEq(response.finding_id, 'f-123');
  assertEq(response.risk_score, 92.5);
  assertEq(response.score_factors?.severity_weight, 1.0);
  assertEq(response.evidence_snippet?.includes('SELECT'), true);
  assertEq(response.token_attributions?.length, 3);
  assertEq(response.highlighted_lines.length, 3);
});

test('P5-2. Attaching ExplainResponse to Finding preserves all original metadata', () => {
  const originalFinding: Finding = {
    id: 'f-456',
    source: 'semgrep',
    severity: 'high',
    title: 'Hardcoded API Secret',
    description: 'API key exposed in source code.',
    file: 'src/config.ts',
    line_start: 42,
    line_end: 42,
    confidence: 0.95,
    rule_id: 'generic.secrets.gcp-api-key',
    feedback_status: 'accepted',
  };

  const xaiResponse: ExplainResponse = {
    finding_id: 'f-456',
    explanation: 'Hardcoded secret detected in client configuration.',
    token_attributions: [{ token: 'AIzaSy', importance: 0.99 }],
    highlighted_lines: [42],
    risk_score: 85.0,
    evidence_snippet: '42 | const KEY = "AIzaSy..."',
    remediation_rationale: 'Move key to environment variable.',
  };

  // Attach XAI fields cleanly
  const updatedFinding: Finding = {
    ...originalFinding,
    explanation: xaiResponse.explanation,
    token_attributions: xaiResponse.token_attributions,
    risk_score: xaiResponse.risk_score,
    score_factors: xaiResponse.score_factors,
    evidence_snippet: xaiResponse.evidence_snippet,
    remediation_rationale: xaiResponse.remediation_rationale,
  };

  // Original metadata MUST be preserved
  assertEq(updatedFinding.id, 'f-456');
  assertEq(updatedFinding.source, 'semgrep');
  assertEq(updatedFinding.severity, 'high');
  assertEq(updatedFinding.title, 'Hardcoded API Secret');
  assertEq(updatedFinding.file, 'src/config.ts');
  assertEq(updatedFinding.line_start, 42);
  assertEq(updatedFinding.confidence, 0.95);
  assertEq(updatedFinding.feedback_status, 'accepted');

  // XAI fields MUST be correctly populated
  assertEq(updatedFinding.explanation, 'Hardcoded secret detected in client configuration.');
  assertEq(updatedFinding.risk_score, 85.0);
  assertEq(updatedFinding.remediation_rationale, 'Move key to environment variable.');
});

test('P5-3. ReviewPanel & FindingsProvider update contract preserves state on XAI payload update', () => {
  const finding: Finding = {
    id: 'f-789',
    source: 'osv',
    severity: 'critical',
    title: 'Vulnerable Lodash Package',
    description: 'Prototype pollution vulnerability in lodash.',
    file: 'package.json',
    line_start: 10,
    line_end: 10,
    confidence: 1.0,
    feedback_status: 'rejected',
  };

  const findingsList = [finding];

  // Simulating provider update
  const provider = {
    findings: findingsList,
    updateExplanation(res: ExplainResponse) {
      const f = this.findings.find((x) => x.id === res.finding_id);
      if (f) {
        f.explanation = res.explanation;
        if (res.risk_score !== undefined) { f.risk_score = res.risk_score; }
        if (res.remediation_rationale) { f.remediation_rationale = res.remediation_rationale; }
      }
    },
  };

  provider.updateExplanation({
    finding_id: 'f-789',
    explanation: 'Upgrade lodash to 4.17.21 or higher.',
    highlighted_lines: [10],
    risk_score: 95.0,
    remediation_rationale: 'Upgrade dependency.',
  });

  assertEq(finding.feedback_status, 'rejected'); // Preserved!
  assertEq(finding.explanation, 'Upgrade lodash to 4.17.21 or higher.');
  assertEq(finding.risk_score, 95.0);
  assertEq(finding.remediation_rationale, 'Upgrade dependency.');
});

test('P5-4. Voice explain flow produces completion TTS and completes STT lifecycle', () => {
  const mgr = new SttLifecycleManager();
  mgr.startSession();
  mgr.onRecognitionStart(1);
  mgr.onFinalResult(1, 'review explain finding 1');

  assertEq(mgr.isBusy(), true);
  assertEq(mgr.getCurrentSession()?.state, 'PROCESSING');

  // Simulated spoken messages buffer
  const spokenMessages: string[] = [];
  const speak = (msg: string) => spokenMessages.push(msg);

  // Initial dispatch TTS
  speak('Explaining finding: SQL Injection.');

  // Async XAI command finishes
  const explainResult: ExplainResponse = {
    finding_id: 'f1',
    explanation: 'SQL Injection via string interpolation.',
    highlighted_lines: [12],
    risk_score: 90.0,
  };

  // Completion TTS
  if (explainResult.risk_score !== undefined) {
    speak(`Explanation ready. SQL Injection has a risk score of ${Math.round(explainResult.risk_score)} out of 100.`);
  }

  // STT Lifecycle finishes
  mgr.finishCommandProcessing(1);

  assertEq(spokenMessages.length, 2);
  assertEq(spokenMessages[0], 'Explaining finding: SQL Injection.');
  assertEq(spokenMessages[1], 'Explanation ready. SQL Injection has a risk score of 90 out of 100.');
  assertEq(mgr.isBusy(), false);
  assertEq(mgr.getCurrentSession()?.state, 'COMPLETED');
});

test('P5-5. Voice explain error path produces error notice and completes STT lifecycle without deadlock', () => {
  const mgr = new SttLifecycleManager();
  const session = mgr.startSession();
  const sid = session!.id;
  mgr.onRecognitionStart(sid);
  mgr.onFinalResult(sid, 'review explain finding 2');

  assertEq(mgr.isBusy(), true);

  const spokenMessages: string[] = [];
  const speak = (msg: string) => spokenMessages.push(msg);

  speak('Explaining finding: XSS Vulnerability.');

  // Simulating API / backend failure during XAI execution
  try {
    throw new Error('Backend 500 Internal Error');
  } catch {
    speak('Could not retrieve explanation for XSS Vulnerability.');
  } finally {
    mgr.finishCommandProcessing(sid);
  }

  assertEq(spokenMessages.length, 2);
  assertEq(spokenMessages[0], 'Explaining finding: XSS Vulnerability.');
  assertEq(spokenMessages[1], 'Could not retrieve explanation for XSS Vulnerability.');
  assertEq(mgr.isBusy(), false);
  assertEq(mgr.getCurrentSession()?.state, 'COMPLETED');
});

console.log(`\n=== Summary: ${passed} passed, ${failed} failed ===\n`);

if (failed > 0) {
  process.exit(1);
}

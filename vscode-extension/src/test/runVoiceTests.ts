/**
 * runVoiceTests.ts
 * Executable Node.js runner for voice parser unit tests.
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

console.log('\n=== Running Voice Intent Parser Unit Tests ===\n');

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

console.log(`\n=== Summary: ${passed} passed, ${failed} failed ===\n`);

if (failed > 0) {
  process.exit(1);
}

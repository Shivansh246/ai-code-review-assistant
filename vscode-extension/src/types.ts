/**
 * types.ts
 * Shared data types / JSON schema agreed between the VS Code extension and
 * the FastAPI backend.  Keep this in sync with Gulshan's backend schema.
 */

/** Severity levels — matches backend enum */
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

/** Every source that can produce a finding */
export type FindingSource = 'semgrep' | 'llm' | 'osv' | 'fused';

/** A single security finding returned by the backend */
export interface Finding {
  id: string;                   // UUID
  source: FindingSource;
  severity: Severity;
  title: string;
  description: string;
  file: string;                 // relative path
  line_start: number;
  line_end: number;
  column_start?: number;
  column_end?: number;
  confidence: number;           // 0.0 – 1.0
  rule_id?: string;             // Semgrep rule id
  cve_id?: string;              // CVE/GHSA id for dependency findings
  package_name?: string;
  package_version?: string;
  explanation?: string;         // XAI / LLM explanation
  fix_suggestion?: string;
  token_attributions?: TokenAttribution[];  // XAI token-level importance
  feedback_status?: FeedbackStatus;
}

/** Token-level attribution from XAI (Captum) */
export interface TokenAttribution {
  token: string;
  importance: number;           // –1.0 to 1.0 (negative = suppressing)
}

/** Developer feedback on a finding */
export type FeedbackStatus = 'accepted' | 'rejected' | 'pending';

export interface FeedbackEvent {
  finding_id: string;
  status: FeedbackStatus;
  timestamp: string;            // ISO 8601
  comment?: string;
}

/** Response from POST /review */
export interface ReviewResponse {
  request_id: string;
  file_path: string;
  findings: Finding[];
  scan_duration_ms: number;
  sources_used: FindingSource[];
}

/** Response from POST /explain */
export interface ExplainResponse {
  finding_id: string;
  explanation: string;
  token_attributions?: TokenAttribution[];
  highlighted_lines: number[];
}

/** Response from POST /fix */
export interface FixResponse {
  finding_id: string;
  fix_suggestion: string;
  confidence: number;
}

/** Voice command payload sent to backend */
export interface VoiceCommand {
  transcript: string;
  command: VoiceCommandType;
  code_context?: string;        // file content when passed along
  file_path?: string;
  finding_id?: string;
}

export type VoiceCommandType =
  | 'review'
  | 'explain'
  | 'show_critical'
  | 'generate_fix'
  | 'accept'
  | 'reject'
  | 'unknown';

/** VAD result from local voice gate (Week 5) */
export interface VadResult {
  is_speech: boolean;
  confidence: number;
  wake_word_detected: boolean;
  transcript?: string;
}

/** Metrics collected for Week 8 evaluation */
export interface VoiceMetrics {
  total_activations: number;
  true_activations: number;
  false_activations: number;
  missed_commands: number;
  avg_latency_ms: number;
  latency_samples: number[];
}

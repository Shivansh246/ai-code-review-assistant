# Pratik's Work — VS Code Extension & Voice Control

## Goal
Build and maintain the VS Code extension that serves as the IDE interface, plus the full voice command pipeline including VAD, wake-word gate, STT, command parsing, and TTS response.

## Owner
Pratik

## Repository path
`vscode-extension/` — all TypeScript source lives here.

## Weekly targets

### Week 1 — Foundation
- Create the extension at `vscode-extension/`
- Implement `aiReview.reviewFile` command
- Extract active file content and send to `POST /review`
- Show returned findings in the sidebar tree view
- **Checkpoint:** Real file → /review → finding appears in IDE

### Week 2 — Severity badges + line navigation
- Display severity badges (critical / high / medium / low / info)
- Clicking a finding navigates to the file:line in the editor
- Inline decorations (coloured gutter + hover message)
- **Checkpoint:** Scan returns ranked findings with visual severity

### Week 3 — Speech-to-text prototype + voice commands
- Open voice webview panel (Web Speech API)
- Commands: review, explain, show critical, generate fix
- Pass recognised transcript to `POST /voice`
- **Checkpoint:** Say "review" → file gets scanned

### Week 4 — Expanded voice + code context
- Pass file content with every voice command (multimodal context)
- Handle command parsing errors gracefully (retry prompt)
- **Checkpoint:** Voice command includes code context in payload

### Week 5 — Local VAD + wake-word gate
- Energy-based VAD: do not forward background noise
- Wake-word check before any heavy processing
- Add basic latency measurement per command
- **Checkpoint:** Background chatter does NOT trigger scans

### Week 6 — Accept / Reject feedback
- Add Accept / Reject buttons in review panel and sidebar
- Voice equivalents ("accept", "reject")
- Show feedback status badge on each finding
- POST to `/feedback` endpoint
- **Checkpoint:** Rejection is stored and shown in UI

### Week 7 — Full TTS loop + coherent UX
- Speak results back via Web Speech Synthesis API
- Ensure voice → command → result → TTS is one coherent loop
- Polish extension UX (loading states, error messages, status bar)
- **Checkpoint:** End-to-end demo: voice request → highlighted findings → spoken result

### Week 8 — Evaluation metrics
- Measure: true activations, false activations, missed commands, response latency
- Expose `aiReview.voiceMetrics` command to show report
- **Checkpoint:** Metrics table ready for final demo

## Execution Scripts
None — this layer is TypeScript, not Python. Built with `npm run compile`.

## Key files
| File | Purpose |
|---|---|
| `src/extension.ts` | Main entry point, command registration |
| `src/types.ts` | Shared JSON schema (keep in sync with backend) |
| `src/apiClient.ts` | HTTP wrapper for FastAPI calls |
| `src/findingsProvider.ts` | Sidebar tree view |
| `src/decorationManager.ts` | Inline editor decorations |
| `src/reviewPanel.ts` | Full webview review panel |
| `src/voiceController.ts` | Voice pipeline (VAD → wake word → STT → TTS) |

## Build & run
```bash
cd vscode-extension
npm install
npm run compile   # or: npm run watch (dev)
# Then press F5 in VS Code to launch Extension Development Host
```

## JSON schema (agreed with backend)
The `types.ts` file defines the schema. When Gulshan updates the backend,
compare `Finding` fields and update both sides together.

## Edge cases
- Backend not running → show warning with link to settings
- Speech recognition not available → show message to use Chrome/Edge
- File not found when navigating → silently skip (backend path may differ)
- Auto-save review disabled by default to avoid unnecessary API calls

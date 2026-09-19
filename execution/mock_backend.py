#!/usr/bin/env python3
"""
mock_backend.py
Lightweight HTTP mock server for testing the AI Code Review VS Code Extension.
Runs without third-party dependencies on http://localhost:8000.
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 8000

class MockBackendHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        if self.path == "/health":
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "healthy", "service": "mock-backend"}).encode())
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode())

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode() if content_length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        file_path = payload.get("file_path", "test.ts")

        if self.path in ("/review", "/review/repo"):
            response = {
                "repo_path": ".",
                "scan_duration_ms": 138,
                "findings": [
                    {
                        "id": "finding-001",
                        "file": file_path,
                        "line_start": 5,
                        "line_end": 7,
                        "severity": "critical",
                        "category": "security",
                        "title": "SQL Injection Detected",
                        "description": "User input directly concatenated into database query without sanitization.",
                        "confidence": 0.98,
                        "rule_id": "SEC-001"
                    },
                    {
                        "id": "finding-002",
                        "file": file_path,
                        "line_start": 15,
                        "line_end": 16,
                        "severity": "high",
                        "category": "security",
                        "title": "Hardcoded API Secret",
                        "description": "High-entropy key string found hardcoded in source file.",
                        "confidence": 0.92,
                        "rule_id": "SEC-002"
                    },
                    {
                        "id": "finding-003",
                        "file": file_path,
                        "line_start": 25,
                        "line_end": 28,
                        "severity": "medium",
                        "category": "performance",
                        "title": "Blocking Synchronous I/O",
                        "description": "Sync I/O operation inside hot execution loop may block event loop.",
                        "confidence": 0.84,
                        "rule_id": "PERF-001"
                    }
                ]
            }
            self._set_headers(200)
            self.wfile.write(json.dumps(response).encode())

        elif self.path == "/explain":
            finding_id = payload.get("finding_id", "finding-001")
            response = {
                "finding_id": finding_id,
                "explanation": f"Taint analysis indicates unsanitized data flows directly from the request parameter to the execution sink on lines 5-7. Confidence: 98%.",
                "token_attributions": [
                    {"token": "query", "score": 0.15},
                    {"token": "+ req.body.id", "score": 0.85}
                ],
                "highlighted_lines": [5, 6, 7]
            }
            self._set_headers(200)
            self.wfile.write(json.dumps(response).encode())

        elif self.path == "/fix":
            finding_id = payload.get("finding_id", "finding-001")
            response = {
                "finding_id": finding_id,
                "fix_suggestion": "const query = 'SELECT * FROM users WHERE id = $1';\nawait client.query(query, [req.body.id]);",
                "diff": "- const query = 'SELECT * FROM users WHERE id = ' + req.body.id;\n+ const query = 'SELECT * FROM users WHERE id = $1';\n+ await client.query(query, [req.body.id]);",
                "confidence": 0.95
            }
            self._set_headers(200)
            self.wfile.write(json.dumps(response).encode())

        elif self.path == "/feedback":
            print(f"[MockBackend] Received feedback: {payload}")
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "recorded", "finding_id": payload.get("finding_id")}).encode())

        elif self.path == "/voice":
            print(f"[MockBackend] Received voice command: {payload}")
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok", "action": "review"}).encode())

        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Endpoint not found"}).encode())

def run():
    server = HTTPServer(("0.0.0.0", PORT), MockBackendHandler)
    print(f"✅ AI Review Mock Backend running on http://localhost:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMock server stopped.")

if __name__ == "__main__":
    run()

/**
 * apiClient.ts
 * Thin HTTP wrapper around the FastAPI backend.
 * All network calls go through here so the rest of the extension
 * never imports axios directly.
 */

import axios, { AxiosInstance, AxiosError } from 'axios';
import * as vscode from 'vscode';
import {
  ReviewResponse,
  ExplainResponse,
  FixResponse,
  FeedbackEvent,
  VoiceCommand,
} from './types';

export class ApiClient {
  private client: AxiosInstance;

  constructor() {
    const url = vscode.workspace
      .getConfiguration('aiReview')
      .get<string>('backendUrl', 'http://localhost:8000');

    this.client = axios.create({
      baseURL: url,
      timeout: 60_000,
      headers: { 'Content-Type': 'application/json' },
    });

    // Log every request (debug level)
    this.client.interceptors.request.use((cfg) => {
      console.log(`[aiReview] → ${cfg.method?.toUpperCase()} ${cfg.url}`);
      return cfg;
    });
  }

  /** Refresh base URL when user changes the setting */
  updateBaseUrl(url: string): void {
    this.client.defaults.baseURL = url;
  }

  /** GET /health – verify backend is reachable */
  async health(): Promise<boolean> {
    try {
      const res = await this.client.get('/health');
      return res.status === 200;
    } catch {
      return false;
    }
  }

  /**
   * POST /review
   * Send file content + path to the backend and get back ranked findings.
   */
  async review(filePath: string, fileContent: string): Promise<ReviewResponse> {
    const res = await this.client.post<ReviewResponse>('/review', {
      file_path: filePath,
      content: fileContent,
    });
    return res.data;
  }

  /**
   * POST /review/repo
   * Trigger a repository-wide scan.
   */
  async reviewRepo(repoPath: string): Promise<ReviewResponse> {
    const res = await this.client.post<ReviewResponse>('/review/repo', {
      repo_path: repoPath,
    });
    return res.data;
  }

  /**
   * POST /explain
   * Get XAI explanation + token attributions for a finding.
   */
  async explain(findingId: string): Promise<ExplainResponse> {
    const res = await this.client.post<ExplainResponse>('/explain', {
      finding_id: findingId,
    });
    return res.data;
  }

  /**
   * POST /fix
   * Request an AI-generated fix suggestion for a finding.
   */
  async fix(findingId: string, fileContent: string): Promise<FixResponse> {
    const res = await this.client.post<FixResponse>('/fix', {
      finding_id: findingId,
      content: fileContent,
    });
    return res.data;
  }

  /**
   * POST /feedback
   * Send accept / reject feedback for adaptive learning (Week 6).
   */
  async sendFeedback(event: FeedbackEvent): Promise<void> {
    await this.client.post('/feedback', event);
  }

  /**
   * POST /voice
   * Send recognised voice command + optional code context.
   */
  async sendVoiceCommand(cmd: VoiceCommand): Promise<unknown> {
    const res = await this.client.post('/voice', cmd);
    return res.data;
  }

  /** Format axios errors for user-facing messages */
  static formatError(err: unknown): string {
    if (err instanceof AxiosError) {
      if (err.code === 'ECONNREFUSED') {
        return 'Backend is not running. Start the FastAPI server first.';
      }
      return err.response?.data?.detail ?? err.message;
    }
    return String(err);
  }
}

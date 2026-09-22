import { RunData } from '../types/api';

// Dev: talk to uvicorn directly on 8100. Production (Docker/nginx): use
// same-origin /api calls that nginx proxies to the backend service.
const API_BASE_URL = import.meta.env.DEV ? 'http://127.0.0.1:8100' : '';

export class ApiService {
  static async getRuns(): Promise<RunData[]> {
    try {
      const response = await fetch(`${API_BASE_URL}/api/runs`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching runs:', error);
      throw error;
    }
  }

  static async getRunById(runId: string): Promise<{
    run: RunData;
    tasks: any[];
  }> {
    try {
      const response = await fetch(`${API_BASE_URL}/api/runs/${runId}`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.error(`Error fetching run ${runId}:`, error);
      throw error;
    }
  }

  static async executeGoal(goal: string): Promise<any> {
    try {
      const response = await fetch(`${API_BASE_URL}/api/execute`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ goal }),
      });
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.error('Error executing goal:', error);
      throw error;
    }
  }

  // Fetch the provider's available models for the picker. Optionally scoped
  // to a custom API key; throws with the server's message on auth failure.
  static async getModels(apiKey?: string): Promise<string[]> {
    const params = new URLSearchParams();
    if (apiKey && apiKey.trim()) params.set('api_key', apiKey.trim());
    const qs = params.toString();
    const response = await fetch(
      `${API_BASE_URL}/api/models${qs ? `?${qs}` : ''}`,
      { headers: { 'Content-Type': 'application/json' } }
    );
    if (!response.ok) {
      let detail = `HTTP error! status: ${response.status}`;
      try {
        const body = await response.json();
        if (body && body.detail) detail = String(body.detail);
      } catch {
        /* non-JSON error body — keep the status message */
      }
      throw new Error(detail);
    }
    const data = await response.json();
    return Array.isArray(data.models) ? data.models : [];
  }
}

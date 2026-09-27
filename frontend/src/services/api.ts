// Dev: talk to uvicorn directly on 8100. Production (Docker/nginx): use
// same-origin /api calls that nginx proxies to the backend service.
const API_BASE_URL = import.meta.env.DEV ? 'http://127.0.0.1:8100' : '';

export class ApiService {
  static async getModels(apiKey?: string): Promise<{
    models: string[];
    provider: string;
    source: 'custom_key' | 'server_env';
    default_model?: string;
  }> {
    const key = (apiKey ?? '').trim();
    const response = await fetch(
      `${API_BASE_URL}/api/models`,
      key
        ? {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: key })
          }
        : { headers: { 'Content-Type': 'application/json' } }
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
    return {
      models: Array.isArray(data.models) ? data.models : [],
      provider: String(data.provider ?? 'Unknown'),
      source: data.source === 'custom_key' ? 'custom_key' : 'server_env',
      default_model: typeof data.default_model === 'string' ? data.default_model : undefined
    };
  }

  static async getTelemetry(): Promise<{
    cpu_percent: number | null;
    mem_percent: number | null;
    uptime_s: number;
  }> {
    const response = await fetch(`${API_BASE_URL}/api/telemetry`);
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
    const data = await response.json();
    return {
      cpu_percent: typeof data.cpu_percent === 'number' ? data.cpu_percent : null,
      mem_percent: typeof data.mem_percent === 'number' ? data.mem_percent : null,
      uptime_s: typeof data.uptime_s === 'number' ? data.uptime_s : 0
    };
  }
}

import { RunData } from '../types/api';

const API_BASE_URL = 'http://127.0.0.1:8100';

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
}

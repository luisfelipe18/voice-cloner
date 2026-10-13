export const API = "/api";

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { cache: "no-store", ...options });
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error("El servidor no esta disponible. Reintenta en unos segundos.");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "No se pudo completar la solicitud.");
  return data;
}

export type Song = {
  song_id: string;
  name: string;
  status: "pending" | "queued" | "processing" | "completed" | "failed" | "legacy";
  language?: string;
  progress: number;
  stage?: string;
  error?: string;
  original_url?: string;
  download_url?: string;
  proof?: { conversion: { engine: string; personal_model?: boolean; model_version?: string; pitch_shift?: number } };
};

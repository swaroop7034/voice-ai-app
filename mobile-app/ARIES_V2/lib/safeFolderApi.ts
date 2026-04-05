const DEFAULT_API_BASE_URL = 'http://10.46.26.175:8000';
const REQUEST_TIMEOUT_MS = Number(process.env.EXPO_PUBLIC_API_TIMEOUT_MS || 20000);

export const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL;

export type SafeFolderAccessResponse = {
  status?: string;
  message?: string;
  aries_text?: string;
  audio?: string;
  access_granted?: boolean;
  reason?: 'keyword_mismatch' | 'voice_mismatch' | 'stt_failed' | 'supabase_read_failed' | string;
  score?: number;
  user_id?: string;
};

export type VaultCategory = 'DOCS' | 'AUDIO' | 'VIDEO' | 'IMAGE';

export type VaultFileItem = {
  id: string;
  name: string;
  stored_name: string;
  category: VaultCategory;
  size_bytes: number;
  size: string;
  date: string;
  created_at: string;
  path: string;
};

export type VaultStats = {
  used_bytes: number;
  total_bytes: number;
  used_gb: number;
  total_gb: number;
  used_human: string;
  total_human: string;
  file_count: number;
};

export type VaultListResponse = {
  status: 'success' | 'error';
  user_id?: string;
  files: VaultFileItem[];
  stats: VaultStats;
  message?: string;
};

export type VaultUploadResponse = {
  status: 'success' | 'error';
  user_id?: string;
  file?: VaultFileItem;
  stats?: VaultStats;
  message?: string;
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
    });

    const data = (await response.json()) as T & { message?: string; reason?: string };
    if (!response.ok) {
      throw new Error(data.message || `Request failed (${response.status})`);
    }

    return data as T;
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      throw new Error('Request timed out. Check backend status and API URL.');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export async function submitSafeFolderAccess(params: {
  audioUri: string;
}): Promise<SafeFolderAccessResponse> {
  const { audioUri } = params;

  const formData = new FormData();
  formData.append('file', {
    uri: audioUri,
    name: 'safe-folder-recording.m4a',
    type: 'audio/m4a',
  } as unknown as Blob);

  return requestJson<SafeFolderAccessResponse>(`${API_BASE_URL}/safe-folder/access`, {
    method: 'POST',
    body: formData,
  });
}

export async function fetchVaultFiles(userId?: string): Promise<VaultListResponse> {
  const url = userId
    ? `${API_BASE_URL}/safe-folder/files?user_id=${encodeURIComponent(userId)}`
    : `${API_BASE_URL}/safe-folder/files`;

  return requestJson<VaultListResponse>(url, { method: 'GET' });
}

export async function uploadVaultFile(params: {
  uri: string;
  name: string;
  mimeType: string;
  userId?: string;
}): Promise<VaultUploadResponse> {
  const { uri, name, mimeType, userId } = params;

  const formData = new FormData();
  formData.append('file', {
    uri,
    name,
    type: mimeType || 'application/octet-stream',
  } as unknown as Blob);

  if (userId) {
    formData.append('user_id', userId);
  }

  return requestJson<VaultUploadResponse>(`${API_BASE_URL}/safe-folder/files/upload`, {
    method: 'POST',
    body: formData,
  });
}

export function getVaultOpenUrl(fileId: string, userId?: string): string {
  const suffix = userId ? `?user_id=${encodeURIComponent(userId)}` : '';
  return `${API_BASE_URL}/safe-folder/files/${encodeURIComponent(fileId)}/open${suffix}`;
}

export function getVaultDownloadUrl(fileId: string, userId?: string): string {
  const suffix = userId ? `?user_id=${encodeURIComponent(userId)}` : '';
  return `${API_BASE_URL}/safe-folder/files/${encodeURIComponent(fileId)}/download${suffix}`;
}

export async function deleteVaultFile(fileId: string, userId?: string): Promise<VaultListResponse> {
  const suffix = userId ? `?user_id=${encodeURIComponent(userId)}` : '';
  return requestJson<VaultListResponse>(
    `${API_BASE_URL}/safe-folder/files/${encodeURIComponent(fileId)}${suffix}`,
    { method: 'DELETE' }
  );
}

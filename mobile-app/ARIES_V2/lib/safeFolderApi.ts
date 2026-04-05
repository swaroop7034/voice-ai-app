const DEFAULT_API_BASE_URL = 'http://192.168.1.4:8000';

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

  const response = await fetch(`${API_BASE_URL}/safe-folder/access`, {
    method: 'POST',
    body: formData,
  });

  const data = (await response.json()) as SafeFolderAccessResponse;

  if (!response.ok && !data.reason && !data.message) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return data;
}
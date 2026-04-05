import { Ionicons } from '@expo/vector-icons';
import { Audio } from 'expo-av';
import * as Haptics from 'expo-haptics';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { submitSafeFolderAccess } from '@/lib/safeFolderApi';

type AccessState = 'idle' | 'recording' | 'submitting';

export default function SafeFolderAccessScreen() {
  const router = useRouter();
  const recordingRef = useRef<Audio.Recording | null>(null);
  const [state, setState] = useState<AccessState>('idle');
  const [lastMessage, setLastMessage] = useState('');

  useEffect(() => {
    return () => {
      recordingRef.current?.stopAndUnloadAsync().catch(() => undefined);
    };
  }, []);

  const startRecording = async () => {
    try {
      const permission = await Audio.requestPermissionsAsync();
      if (!permission.granted) {
        Alert.alert('Permission required', 'Microphone permission is needed to verify your voice.');
        return;
      }

      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });

      const { recording } = await Audio.Recording.createAsync(Audio.RecordingOptionsPresets.HIGH_QUALITY);
      recordingRef.current = recording;
      setState('recording');
      setLastMessage('Listening for the keyword...');
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    } catch (error) {
      console.error('[SafeFolder] Failed to start recording', error);
      Alert.alert('Recording error', 'Could not start microphone recording.');
      setState('idle');
    }
  };

  const stopAndSubmit = async () => {
    const recording = recordingRef.current;
    if (!recording) {
      return;
    }

    try {
      setState('submitting');
      await recording.stopAndUnloadAsync();
      const audioUri = recording.getURI();
      recordingRef.current = null;

      if (!audioUri) {
        Alert.alert('No audio', 'Could not capture the recording.');
        setState('idle');
        return;
      }

      const response = await submitSafeFolderAccess({
        audioUri,
      });
      console.log('[SafeFolder] Backend response:', response);

      if (response.status === 'enrolled') {
        Alert.alert('Success', 'Voice registered successfully');
        setLastMessage('Voice registered successfully');
        setState('idle');
        return;
      }

      if (response.access_granted) {
        setState('idle');
        router.replace('/safe-folder-screen');
        return;
      }

      const errorMessage =
        response.message
          || (response.reason === 'keyword_mismatch'
            ? 'Wrong keyword'
            : response.reason === 'voice_mismatch'
              ? 'Voice not recognized'
              : response.reason === 'stt_failed'
                ? 'Could not understand the speech'
                : response.reason === 'supabase_read_failed'
                  ? 'Voice database unavailable'
                  : 'Access denied');

      setLastMessage(errorMessage);
      Alert.alert('Access denied', errorMessage);
      setState('idle');
    } catch (error) {
      console.error('[SafeFolder] Submission failed', error);
      setState('idle');
      Alert.alert('Network error', 'Unable to verify access right now.');
    }
  };

  const handlePressIn = async () => {
    if (state !== 'idle') {
      return;
    }
    await startRecording();
  };

  const handlePressOut = async () => {
    if (state === 'recording') {
      await stopAndSubmit();
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.backgroundGlow} />

      <View style={styles.card}>
        <Text style={styles.title}>Safe Folder Access</Text>
        <Text style={styles.subtitle}>Voice only. The backend uses your calendar Gmail automatically.</Text>

        <Pressable
          onPressIn={handlePressIn}
          onPressOut={handlePressOut}
          style={({ pressed }) => [styles.micButton, pressed && styles.micButtonPressed]}
        >
          {state === 'submitting' ? (
            <ActivityIndicator color="#06121f" />
          ) : (
            <Ionicons name={state === 'recording' ? 'mic' : 'mic-outline'} size={34} color="#06121f" />
          )}
        </Pressable>

        <Text style={styles.buttonHint}>
          {state === 'recording' ? 'Release to submit voice access' : 'Press and hold, then say “safe folder”'}
        </Text>

        {!!lastMessage && <Text style={styles.statusText}>{lastMessage}</Text>}
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#02050a',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  backgroundGlow: {
    position: 'absolute',
    width: 360,
    height: 360,
    borderRadius: 180,
    backgroundColor: '#00d4ff18',
    top: -120,
    right: -80,
  },
  card: {
    width: '100%',
    maxWidth: 420,
    backgroundColor: '#0b1220',
    borderWidth: 1,
    borderColor: '#00d4ff2a',
    borderRadius: 24,
    padding: 24,
    shadowColor: '#000',
    shadowOpacity: 0.35,
    shadowRadius: 24,
    elevation: 8,
  },
  title: {
    color: '#f8fafc',
    fontSize: 30,
    fontWeight: '800',
    textAlign: 'center',
  },
  subtitle: {
    color: '#93c5fd',
    fontSize: 14,
    textAlign: 'center',
    marginTop: 10,
    marginBottom: 24,
    lineHeight: 20,
  },
  micButton: {
    alignSelf: 'center',
    width: 88,
    height: 88,
    borderRadius: 44,
    backgroundColor: '#00d4ff',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 8,
  },
  micButtonPressed: {
    transform: [{ scale: 0.97 }],
    opacity: 0.92,
  },
  buttonHint: {
    color: '#94a3b8',
    marginTop: 16,
    textAlign: 'center',
    fontSize: 13,
  },
  statusText: {
    color: '#e2e8f0',
    marginTop: 14,
    textAlign: 'center',
    fontSize: 14,
  },
});
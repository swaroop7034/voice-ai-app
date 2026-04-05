import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { ProfileCard } from '@/components/settings/ProfileCard';
import { SettingsSection } from '@/components/settings/SettingsSection';
import { ToggleSwitch } from '@/components/settings/ToggleSwitch';

const API_BASE = 'http://10.39.74.10:8000';

type UserProfile = {
  name: string;
  email: string;
  avatarUrl?: string;
};

type SettingsState = {
  memory: boolean;
  saveConversations: boolean;
  scheduling: boolean;
  darkMode: boolean;
  tts: boolean;
  notifications: boolean;
};

const DEFAULT_SETTINGS: SettingsState = {
  memory: true,
  saveConversations: true,
  scheduling: true,
  darkMode: false,
  tts: true,
  notifications: true,
};

export default function SettingsPage() {
  const [loadingProfile, setLoadingProfile] = useState(false);
  const [busy, setBusy] = useState(false);
  const [profile, setProfile] = useState<UserProfile>({
    name: 'Swaroop',
    email: 'example@gmail.com',
  });
  const [settings, setSettings] = useState<SettingsState>(DEFAULT_SETTINGS);

  useEffect(() => {
    void fetchProfile();
    console.log('[STEP] SETTINGS_OPENED');
  }, []);

  const appVersion = useMemo(() => 'v1.0.0', []);

  const fetchProfile = async () => {
    setLoadingProfile(true);
    try {
      const response = await fetch(`${API_BASE}/user/profile`);
      if (response.ok) {
        const data = await response.json();
        setProfile({
          name: data?.name || 'Swaroop',
          email: data?.email || 'example@gmail.com',
          avatarUrl: data?.avatarUrl,
        });
      }
    } catch {
      // Keep fallback profile when endpoint is unavailable.
    } finally {
      setLoadingProfile(false);
    }
  };

  const updateSettings = async (patch: Partial<SettingsState>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    try {
      await fetch(`${API_BASE}/settings/update`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      });
    } catch {
      // Keep local state even if backend endpoint is not ready.
    }
  };

  const triggerVoiceReset = async () => {
    Alert.alert(
      'Reset Voice',
      'This will require re-registration of your voice profile. Continue?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Continue',
          style: 'destructive',
          onPress: async () => {
            setBusy(true);
            try {
              await fetch(`${API_BASE}/voice/reset`, { method: 'POST' });
              console.log('[STEP] VOICE_RESET_TRIGGERED');
              Alert.alert('Started', 'Voice reset has been triggered.');
            } catch {
              Alert.alert('Unavailable', 'Voice reset endpoint is not available yet.');
            } finally {
              setBusy(false);
            }
          },
        },
      ]
    );
  };

  const clearMemory = async () => {
    Alert.alert('Clear Memory', 'This action will clear stored memory. Continue?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Clear',
        style: 'destructive',
        onPress: async () => {
          setBusy(true);
          try {
            await fetch(`${API_BASE}/memory/clear`, { method: 'POST' });
            console.log('[STEP] MEMORY_CLEARED');
            Alert.alert('Done', 'Memory has been cleared.');
          } catch {
            Alert.alert('Unavailable', 'Memory clear endpoint is not available yet.');
          } finally {
            setBusy(false);
          }
        },
      },
    ]);
  };

  const openCalendarView = () => {
    Alert.alert('Calendar', 'Connected calendar view can be opened from backend route.');
  };

  const disconnectCalendar = () => {
    Alert.alert('Calendar', 'Calendar disconnect placeholder action triggered.');
  };

  const editProfile = () => {
    Alert.alert('Profile', 'Edit profile UI placeholder.');
  };

  return (
    <View style={styles.container}>
      <LinearGradient colors={['#02050a', '#050a15', '#000']} style={StyleSheet.absoluteFill} />

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <Text style={styles.pageTitle}>SETTINGS</Text>

        <ProfileCard
          name={loadingProfile ? 'Loading...' : profile.name}
          email={profile.email}
          avatarUrl={profile.avatarUrl}
          onEdit={editProfile}
        />

        <SettingsSection title="Voice Settings" icon="mic-outline">
          <Text style={styles.description}>Re-register your voice authentication.</Text>
          <Pressable style={styles.primaryButton} disabled={busy} onPress={triggerVoiceReset}>
            <Ionicons name="refresh" size={16} color="#06121f" />
            <Text style={styles.primaryButtonText}>Reset Voice</Text>
          </Pressable>
        </SettingsSection>

        <SettingsSection title="AI / Memory" icon="sparkles-outline">
          <ToggleSwitch
            label="Enable Memory"
            value={settings.memory}
            onValueChange={(value) => void updateSettings({ memory: value })}
          />
          <ToggleSwitch
            label="Save Conversations"
            value={settings.saveConversations}
            onValueChange={(value) => void updateSettings({ saveConversations: value })}
          />
          <Pressable style={styles.secondaryButton} disabled={busy} onPress={clearMemory}>
            <Text style={styles.secondaryButtonText}>Clear Memory</Text>
          </Pressable>
        </SettingsSection>

        <SettingsSection title="Calendar" icon="calendar-outline">
          <ToggleSwitch
            label="Enable Scheduling"
            value={settings.scheduling}
            onValueChange={(value) => void updateSettings({ scheduling: value })}
          />
          <View style={styles.horizontalRow}>
            <Pressable style={styles.smallButton} onPress={openCalendarView}>
              <Text style={styles.smallButtonText}>View Connected Calendar</Text>
            </Pressable>
            <Pressable style={[styles.smallButton, styles.dangerButton]} onPress={disconnectCalendar}>
              <Text style={styles.dangerButtonText}>Disconnect Calendar</Text>
            </Pressable>
          </View>
        </SettingsSection>

        <SettingsSection title="System" icon="settings-outline">
          <ToggleSwitch
            label="Dark Mode"
            value={settings.darkMode}
            onValueChange={(value) => void updateSettings({ darkMode: value })}
          />
          <ToggleSwitch
            label="Voice Feedback (TTS)"
            value={settings.tts}
            onValueChange={(value) => void updateSettings({ tts: value })}
          />
          <ToggleSwitch
            label="Notifications"
            value={settings.notifications}
            onValueChange={(value) => void updateSettings({ notifications: value })}
          />
        </SettingsSection>

        <SettingsSection title="About" icon="information-circle-outline">
          <Text style={styles.metaText}>App Version: {appVersion}</Text>
          <Text style={styles.metaText}>Developer: ARIES Voice AI Team</Text>
          <Pressable onPress={() => void Linking.openURL('https://example.com/privacy')}>
            <Text style={styles.linkText}>Privacy Policy</Text>
          </Pressable>
        </SettingsSection>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 16,
    paddingTop: 20,
    paddingBottom: 32,
  },
  pageTitle: {
    color: '#e6f6ff',
    fontSize: 22,
    fontWeight: '900',
    letterSpacing: 1.5,
    marginBottom: 14,
  },
  description: {
    color: '#93a9c0',
    fontSize: 12,
  },
  primaryButton: {
    marginTop: 8,
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: '#00d4ff',
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  primaryButtonText: {
    color: '#06121f',
    fontSize: 13,
    fontWeight: '900',
  },
  secondaryButton: {
    marginTop: 4,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#ef4444',
    paddingHorizontal: 12,
    paddingVertical: 10,
    alignSelf: 'flex-start',
  },
  secondaryButtonText: {
    color: '#ef4444',
    fontSize: 13,
    fontWeight: '700',
  },
  horizontalRow: {
    flexDirection: 'row',
    gap: 8,
    flexWrap: 'wrap',
  },
  smallButton: {
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#00d4ff55',
    paddingHorizontal: 10,
    paddingVertical: 10,
    backgroundColor: 'rgba(0, 212, 255, 0.08)',
  },
  smallButtonText: {
    color: '#cdefff',
    fontSize: 12,
    fontWeight: '700',
  },
  dangerButton: {
    borderColor: '#ef444444',
    backgroundColor: 'rgba(239, 68, 68, 0.08)',
  },
  dangerButtonText: {
    color: '#fecaca',
    fontSize: 12,
    fontWeight: '700',
  },
  metaText: {
    color: '#bfd0e2',
    fontSize: 12,
  },
  linkText: {
    color: '#60a5fa',
    fontSize: 12,
    textDecorationLine: 'underline',
    marginTop: 2,
  },
});

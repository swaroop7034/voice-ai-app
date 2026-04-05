import { Ionicons } from '@expo/vector-icons';
import React, { ReactNode } from 'react';
import { StyleSheet, Text, View } from 'react-native';

type SettingsSectionProps = {
  title: string;
  icon: keyof typeof Ionicons.glyphMap;
  children: ReactNode;
};

export function SettingsSection({ title, icon, children }: SettingsSectionProps) {
  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <Ionicons name={icon} size={18} color="#00d4ff" />
        <Text style={styles.title}>{title}</Text>
      </View>
      <View style={styles.content}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: 'rgba(6, 18, 31, 0.92)',
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#00d4ff28',
    padding: 14,
    marginBottom: 14,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  title: {
    color: '#e2f3ff',
    fontSize: 14,
    letterSpacing: 0.5,
    fontWeight: '800',
  },
  content: {
    gap: 8,
  },
});

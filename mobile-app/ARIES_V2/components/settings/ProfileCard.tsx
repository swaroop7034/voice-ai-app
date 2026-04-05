import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';

type ProfileCardProps = {
  name: string;
  email?: string;
  avatarUrl?: string;
  onEdit: () => void;
};

export function ProfileCard({ name, email, avatarUrl, onEdit }: ProfileCardProps) {
  return (
    <View style={styles.card}>
      {avatarUrl ? (
        <Image source={{ uri: avatarUrl }} style={styles.avatar} />
      ) : (
        <View style={styles.avatarFallback}>
          <Ionicons name="person" size={28} color="#00d4ff" />
        </View>
      )}

      <View style={styles.infoWrap}>
        <Text style={styles.name}>{name}</Text>
        {!!email && <Text style={styles.email}>{email}</Text>}
      </View>

      <Pressable style={styles.editButton} onPress={onEdit}>
        <Ionicons name="create-outline" size={16} color="#06121f" />
        <Text style={styles.editText}>Edit</Text>
      </Pressable>
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
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginBottom: 14,
  },
  avatar: {
    width: 56,
    height: 56,
    borderRadius: 28,
    borderWidth: 1,
    borderColor: '#00d4ff66',
  },
  avatarFallback: {
    width: 56,
    height: 56,
    borderRadius: 28,
    borderWidth: 1,
    borderColor: '#00d4ff66',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#071622',
  },
  infoWrap: {
    flex: 1,
  },
  name: {
    color: '#e2f3ff',
    fontSize: 16,
    fontWeight: '800',
  },
  email: {
    color: '#94a6bc',
    fontSize: 12,
    marginTop: 2,
  },
  editButton: {
    backgroundColor: '#00d4ff',
    borderRadius: 999,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  editText: {
    color: '#06121f',
    fontSize: 12,
    fontWeight: '800',
  },
});

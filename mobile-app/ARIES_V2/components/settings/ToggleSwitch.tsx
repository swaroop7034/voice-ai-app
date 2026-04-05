import React from 'react';
import { StyleSheet, Switch, Text, View } from 'react-native';

type ToggleSwitchProps = {
  label: string;
  description?: string;
  value: boolean;
  onValueChange: (value: boolean) => void;
};

export function ToggleSwitch({ label, description, value, onValueChange }: ToggleSwitchProps) {
  return (
    <View style={styles.row}>
      <View style={styles.textWrap}>
        <Text style={styles.label}>{label}</Text>
        {!!description && <Text style={styles.description}>{description}</Text>}
      </View>
      <Switch
        value={value}
        onValueChange={onValueChange}
        thumbColor={value ? '#06121f' : '#f4f3f4'}
        trackColor={{ false: '#364153', true: '#00d4ff' }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 10,
    gap: 12,
  },
  textWrap: {
    flex: 1,
  },
  label: {
    color: '#f8fafc',
    fontSize: 15,
    fontWeight: '700',
  },
  description: {
    color: '#9fb2c8',
    fontSize: 12,
    marginTop: 4,
  },
});

import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { C, shadow } from '../theme';

export default function MetricCard({ label, value, valueStyle, small }) {
  return (
    <View style={[styles.card, small && styles.cardSmall]}>
      <Text style={styles.label}>{label}</Text>
      <Text style={[styles.value, small && styles.valueSmall, valueStyle]}>{value ?? '—'}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: C.card,
    borderWidth: 1,
    borderColor: C.border,
    borderRadius: 6,
    padding: 16,
    flex: 1,
    ...shadow,
  },
  cardSmall: { padding: 12 },
  label: {
    fontSize: 11,
    color: C.text3,
    marginBottom: 6,
    textAlign: 'right',
  },
  value: {
    fontSize: 28,
    fontWeight: '700',
    color: C.text,
    textAlign: 'right',
  },
  valueSmall: { fontSize: 18 },
});

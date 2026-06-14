import React, { useState, useCallback } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Alert,
  ScrollView, Switch, Linking,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import * as LocalAuthentication from 'expo-local-authentication';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { C } from '../theme';
import { postReset, postGoLive } from '../api/client';
import { API_BASE_URL } from '../config';

export default function SettingsScreen() {
  const insets = useSafeAreaInsets();
  const [biometricEnabled, setBiometricEnabled] = useState(false);
  const [hapticEnabled, setHapticEnabled] = useState(true);

  const handleGoLive = useCallback(() => {
    Alert.alert(
      '⚠️ מעבר למסחר אמיתי',
      'פעולה זו תפעיל מסחר עם כסף אמיתי. האם אתה בטוח?',
      [
        { text: 'ביטול', style: 'cancel' },
        {
          text: 'מעבר לחי — אני בטוח',
          style: 'destructive',
          onPress: () => {
            Alert.alert('אישור אחרון', 'זוהי הפעולה האחרונה לפני מסחר חי. הכסף שלך בסיכון.', [
              { text: 'ביטול', style: 'cancel' },
              {
                text: 'אני מבין — הפעל מסחר חי',
                style: 'destructive',
                onPress: async () => {
                  try {
                    if (biometricEnabled) {
                      const result = await LocalAuthentication.authenticateAsync({
                        promptMessage: 'אמת זהות להפעלת מסחר חי',
                      });
                      if (!result.success) return;
                    }
                    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
                    await postGoLive();
                    Alert.alert('✓ מסחר חי פעיל', 'הסוכן מסחר עכשיו עם כסף אמיתי.');
                  } catch (e) {
                    Alert.alert('שגיאה', e.message);
                  }
                },
              },
            ]);
          },
        },
      ]
    );
  }, [biometricEnabled]);

  const handleReset = useCallback(() => {
    Alert.alert(
      'איפוס יתרה',
      'יתרה תחזור ל-$18.00 וכל הפוזיציות ייסגרו.',
      [
        { text: 'ביטול', style: 'cancel' },
        {
          text: 'אפס',
          style: 'destructive',
          onPress: async () => {
            try {
              Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
              await postReset();
              Alert.alert('✓ איפוס הושלם', 'יתרה חודשה ל-$18.00');
            } catch (e) {
              Alert.alert('שגיאה', e.message);
            }
          },
        },
      ]
    );
  }, []);

  const Row = ({ label, value, onPress, danger, right }) => (
    <TouchableOpacity style={styles.row} onPress={onPress} disabled={!onPress}>
      <Text style={[styles.rowLabel, danger && { color: C.red }]}>{label}</Text>
      {right || (value ? <Text style={styles.rowValue}>{value}</Text> : null)}
    </TouchableOpacity>
  );

  return (
    <ScrollView
      style={styles.scroll}
      contentContainerStyle={[styles.container, { paddingTop: insets.top + 8 }]}
    >
      <Text style={styles.pageTitle}>הגדרות</Text>

      {/* Connection */}
      <Text style={styles.section}>חיבור</Text>
      <View style={styles.card}>
        <Row label="Railway API" value={API_BASE_URL.replace('https://', '')} />
        <Row label="בדוק חיבור" onPress={async () => {
          try {
            const r = await fetch(`${API_BASE_URL}/health`);
            const d = await r.json();
            Alert.alert('✓ מחובר', `מצב: ${d.status} | מצב: ${d.mode}`);
          } catch {
            Alert.alert('❌ לא מחובר', 'בדוק את כתובת ה-API ב-src/config.js');
          }
        }} />
        <Row label="פתח דשבורד" onPress={() => Linking.openURL(API_BASE_URL)} />
      </View>

      {/* App */}
      <Text style={styles.section}>אפליקציה</Text>
      <View style={styles.card}>
        <Row label="רטט כשנפתחת עסקה" right={
          <Switch value={hapticEnabled} onValueChange={v => { setHapticEnabled(v); Haptics.selectionAsync(); }}
            trackColor={{ false: C.border, true: C.green }} thumbColor={C.text} />
        } />
        <Row label="Face ID לפני מסחר חי" right={
          <Switch value={biometricEnabled} onValueChange={async v => {
            if (v) {
              const supp = await LocalAuthentication.hasHardwareAsync();
              if (!supp) { Alert.alert('לא נתמך', 'Face ID לא זמין במכשיר זה'); return; }
            }
            setBiometricEnabled(v); Haptics.selectionAsync();
          }} trackColor={{ false: C.border, true: C.green }} thumbColor={C.text} />
        } />
      </View>

      {/* Trading controls */}
      <Text style={styles.section}>שליטה במסחר</Text>
      <View style={styles.card}>
        <Row label="↺ איפוס יתרה ל-$18.00" onPress={handleReset} />
        <Row label="⚡ הפעל מסחר חי" onPress={handleGoLive} danger />
      </View>

      {/* About */}
      <Text style={styles.section}>אודות</Text>
      <View style={styles.card}>
        <Row label="גרסה" value="1.0.0" />
        <Row label="מנוע" value="WealthShock Agent v3.0" />
        <Row label="אסטרטגיות" value="8 (6 קלאסיות + 2 exploits)" />
      </View>

      <Text style={styles.disclaimer}>
        כל הנתונים בהדמיה בלבד עד הפעלת מסחר חי.{'\n'}
        השקעות בשוקי ניבוי כרוכות בסיכון לאובדן ההון.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll:     { flex: 1, backgroundColor: C.bg },
  container:  { paddingHorizontal: 16, paddingBottom: 40 },
  pageTitle:  { fontSize: 24, fontWeight: '700', color: C.text, textAlign: 'right', marginBottom: 24 },
  section:    { fontSize: 12, color: C.text4, textAlign: 'right', marginBottom: 8, marginTop: 16 },
  card:       { backgroundColor: C.card, borderRadius: 8, borderWidth: 1, borderColor: C.border, overflow: 'hidden' },
  row:        { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderColor: C.border },
  rowLabel:   { fontSize: 15, color: C.text },
  rowValue:   { fontSize: 13, color: C.text3 },
  disclaimer: { fontSize: 11, color: C.text4, textAlign: 'center', marginTop: 24, lineHeight: 18 },
});

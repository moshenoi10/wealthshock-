import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, ScrollView, FlatList, StyleSheet,
  TouchableOpacity, RefreshControl, Alert,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { C, fmtUSD, fmtTime } from '../theme';
import { getDetective, triggerDetectiveScan } from '../api/client';

const GROUP_HE = { timing: 'תזמון', size: 'גודל', category: 'קטגוריה' };

function WinRateBar({ wr }) {
  const pct = Math.min(wr * 100, 100);
  const color = wr >= 0.6 ? C.green : wr >= 0.45 ? C.yellow : C.red;
  return (
    <View style={styles.barRow}>
      <View style={styles.barBg}>
        <View style={[styles.barFill, { width: `${pct}%`, backgroundColor: color }]} />
      </View>
      <Text style={[styles.barPct, { color }]}>{pct.toFixed(0)}%</Text>
    </View>
  );
}

export default function DetectiveScreen() {
  const insets = useSafeAreaInsets();
  const [data, setData] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [scanning, setScanning] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await getDetective();
      setData(r.data);
    } catch {}
    setRefreshing(false);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const startScan = async () => {
    Alert.alert('סריקת בלש', 'הסריקה תיקח 2-3 דקות. הנתונים יתעדכנו ברקע.', [
      { text: 'ביטול', style: 'cancel' },
      {
        text: 'סרוק', onPress: async () => {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
          setScanning(true);
          try {
            await triggerDetectiveScan();
            setTimeout(() => { refresh(); setScanning(false); }, 35000);
          } catch { setScanning(false); }
        },
      },
    ]);
  };

  const d = data || {};
  const top3 = d.top3_patterns || [];
  const patterns = d.patterns || [];
  const wallets = d.top_wallets || [];
  const categories = d.categories || [];

  return (
    <ScrollView
      style={styles.scroll}
      contentContainerStyle={[styles.container, { paddingTop: insets.top + 8 }]}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); refresh(); }} tintColor={C.green} />}
    >
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>🔍 בלש · ניתוח שוק</Text>
        <TouchableOpacity style={[styles.scanBtn, scanning && styles.scanBtnBusy]} onPress={startScan} disabled={scanning}>
          <Text style={styles.scanBtnText}>{scanning ? '⏳ סורק...' : '+ סרוק עכשיו'}</Text>
        </TouchableOpacity>
      </View>

      {/* Status row */}
      <View style={styles.statRow}>
        {[
          { label: 'עסקאות', value: (d.total_trades || 0).toLocaleString() },
          { label: 'שווקים', value: d.total_markets || 0 },
          { label: 'ארנקים', value: d.total_wallets || 0 },
          { label: 'סטטוס',  value: d.status === 'הושלם' ? '✓' : d.status || '—',
            color: d.status === 'הושלם' ? C.green : C.yellow },
        ].map(s => (
          <View key={s.label} style={styles.statCard}>
            <Text style={styles.statLbl}>{s.label}</Text>
            <Text style={[styles.statVal, s.color && { color: s.color }]}>{String(s.value)}</Text>
          </View>
        ))}
      </View>

      {/* Top 3 winning patterns */}
      {top3.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>3 הדפוסים המנצחים ביותר</Text>
          {top3.map((p, i) => (
            <View key={p.label} style={styles.patCard}>
              <Text style={styles.patRank}>{['🥇', '🥈', '🥉'][i]}</Text>
              <View style={styles.patBody}>
                <Text style={styles.patLabel}>{p.label}</Text>
                <WinRateBar wr={p.win_rate} />
                <Text style={styles.patMeta}>{p.trades} עסקאות · {GROUP_HE[p.group] || p.group}</Text>
              </View>
            </View>
          ))}
        </View>
      )}

      {/* Category breakdown */}
      {categories.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>פירוט לפי קטגוריה</Text>
          {categories.map(c => (
            <View key={c.category} style={styles.catRow}>
              <Text style={styles.catName}>{c.category}</Text>
              <WinRateBar wr={c.win_rate} />
              <Text style={styles.catMeta}>{c.trades} עסקאות</Text>
            </View>
          ))}
        </View>
      )}

      {/* All patterns */}
      {patterns.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>כל הדפוסים</Text>
          {patterns.slice(0, 12).map((p, i) => (
            <View key={`${p.label}-${i}`} style={styles.patRow}>
              <Text style={styles.patRowLbl}>{p.label}</Text>
              <WinRateBar wr={p.win_rate} />
              <Text style={styles.patRowN}>{p.trades}</Text>
            </View>
          ))}
        </View>
      )}

      {/* Wallet leaderboard */}
      {wallets.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>ארנקים מובילים 🐋</Text>
          {wallets.slice(0, 10).map((w, i) => (
            <View key={w.wallet} style={styles.walletRow}>
              <Text style={styles.walletRank}>{i + 1}</Text>
              <Text style={styles.walletAddr}>{w.wallet}{w.is_whale ? ' 🐋' : ''}</Text>
              <View style={styles.walletMeta}>
                <Text style={styles.walletWr}>{(w.win_rate * 100).toFixed(0)}%</Text>
                <Text style={[styles.walletPnl, { color: w.net_pnl >= 0 ? C.green : C.red }]}>
                  {w.net_pnl >= 0 ? '+' : ''}{fmtUSD(w.net_pnl)}
                </Text>
              </View>
            </View>
          ))}
        </View>
      )}

      {!data && (
        <View style={styles.emptyState}>
          <Text style={styles.emptyIcon}>🔍</Text>
          <Text style={styles.emptyText}>לחץ "סרוק עכשיו" להפעלת הניתוח{'\n'}הסריקה תיקח כ-2 דקות</Text>
        </View>
      )}

      {d.last_scan && (
        <Text style={styles.lastScan}>סריקה אחרונה: {fmtTime(d.last_scan)}</Text>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll:     { flex: 1, backgroundColor: C.bg },
  container:  { paddingHorizontal: 16, paddingBottom: 32 },

  header:       { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 },
  title:        { fontSize: 18, fontWeight: '700', color: C.text },
  scanBtn:      { backgroundColor: 'rgba(74,144,255,0.15)', borderWidth: 1, borderColor: C.blue, borderRadius: 6, paddingHorizontal: 12, paddingVertical: 8 },
  scanBtnBusy:  { opacity: 0.5 },
  scanBtnText:  { color: C.blue, fontSize: 13, fontWeight: '600' },

  statRow:  { flexDirection: 'row', gap: 8, marginBottom: 16 },
  statCard: { flex: 1, backgroundColor: C.card, borderRadius: 6, borderWidth: 1, borderColor: C.border, padding: 10, alignItems: 'center' },
  statLbl:  { fontSize: 10, color: C.text3, marginBottom: 4 },
  statVal:  { fontSize: 16, fontWeight: '700', color: C.text },

  card:      { backgroundColor: C.card, borderRadius: 6, borderWidth: 1, borderColor: C.border, padding: 14, marginBottom: 12 },
  cardTitle: { fontSize: 13, color: C.text3, marginBottom: 12, textAlign: 'right' },

  patCard:  { flexDirection: 'row', alignItems: 'flex-start', marginBottom: 12, gap: 10 },
  patRank:  { fontSize: 22 },
  patBody:  { flex: 1 },
  patLabel: { fontSize: 13, color: C.text, textAlign: 'right', marginBottom: 6 },
  patMeta:  { fontSize: 10, color: C.text4, textAlign: 'right', marginTop: 4 },

  barRow:  { flexDirection: 'row', alignItems: 'center', gap: 8 },
  barBg:   { flex: 1, height: 6, backgroundColor: '#222', borderRadius: 3, overflow: 'hidden', direction: 'ltr' },
  barFill: { height: '100%', borderRadius: 3 },
  barPct:  { fontSize: 12, fontFamily: 'Courier New', width: 36, textAlign: 'right' },

  catRow:  { marginBottom: 10 },
  catName: { fontSize: 12, color: C.text2, textAlign: 'right', marginBottom: 4 },
  catMeta: { fontSize: 10, color: C.text4, textAlign: 'right', marginTop: 2 },

  patRow:    { flexDirection: 'row', alignItems: 'center', marginBottom: 8, gap: 8 },
  patRowLbl: { fontSize: 11, color: C.text2, textAlign: 'right', flex: 1 },
  patRowN:   { fontSize: 11, color: C.text4, fontFamily: 'Courier New', width: 36, textAlign: 'right' },

  walletRow:  { flexDirection: 'row', alignItems: 'center', paddingVertical: 8, borderBottomWidth: 1, borderColor: C.border, gap: 8 },
  walletRank: { fontSize: 12, color: C.text4, width: 20 },
  walletAddr: { flex: 1, fontSize: 11, color: C.text3, fontFamily: 'Courier New' },
  walletMeta: { alignItems: 'flex-end' },
  walletWr:   { fontSize: 11, color: C.text3 },
  walletPnl:  { fontSize: 13, fontWeight: '600', fontFamily: 'Courier New' },

  emptyState: { alignItems: 'center', paddingVertical: 60 },
  emptyIcon:  { fontSize: 48, marginBottom: 16 },
  emptyText:  { fontSize: 14, color: C.text3, textAlign: 'center', lineHeight: 22 },
  lastScan:   { fontSize: 11, color: C.text4, textAlign: 'center', marginTop: 8 },
});

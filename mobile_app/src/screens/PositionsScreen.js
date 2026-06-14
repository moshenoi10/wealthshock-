import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, FlatList, StyleSheet, TouchableOpacity,
  Alert, RefreshControl,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { C, pnlColor, fmtUSD, fmtTime } from '../theme';
import SparkLine from '../components/SparkLine';
import { getPositions, getTrades } from '../api/client';
import { POLL_INTERVAL_MS } from '../config';

export default function PositionsScreen() {
  const insets = useSafeAreaInsets();
  const [positions, setPositions] = useState([]);
  const [trades,    setTrades]    = useState([]);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState('open'); // 'open' | 'closed'

  const refresh = useCallback(async () => {
    try {
      const [pRes, tRes] = await Promise.all([getPositions(), getTrades()]);
      setPositions(pRes.data || []);
      setTrades((tRes.data || []).filter(t => t.status === 'closed' || t.pnl != null).slice(0, 50));
    } catch {}
    setRefreshing(false);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const renderPosition = ({ item: pos }) => {
    const up = pos.unrealized_pnl ?? 0;
    const pctChange = pos.entry_price > 0
      ? ((pos.current_price - pos.entry_price) / pos.entry_price)
      : 0;
    return (
      <View style={styles.posCard}>
        <View style={styles.posHeader}>
          <Text style={styles.posQ} numberOfLines={2}>{pos.market_question}</Text>
          <Text style={[styles.posUp, { color: pnlColor(up) }]}>
            {up >= 0 ? '+' : ''}{fmtUSD(up, 4)}
          </Text>
        </View>
        <View style={styles.posRow}>
          <View>
            <Text style={styles.posLbl}>כניסה</Text>
            <Text style={styles.posPx}>{pos.entry_price?.toFixed(4)}</Text>
          </View>
          <View>
            <Text style={styles.posLbl}>עכשיו</Text>
            <Text style={[styles.posPx, { color: pnlColor(pctChange) }]}>
              {pos.current_price?.toFixed(4)}
            </Text>
          </View>
          <View>
            <Text style={styles.posLbl}>גודל</Text>
            <Text style={styles.posPx}>{fmtUSD(pos.size)}</Text>
          </View>
          <View>
            <Text style={styles.posLbl}>שינוי</Text>
            <Text style={[styles.posPx, { color: pnlColor(pctChange) }]}>
              {pctChange >= 0 ? '+' : ''}{(pctChange * 100).toFixed(2)}%
            </Text>
          </View>
        </View>
        {pos.price_history?.length > 1 && (
          <View style={styles.sparkWrap}>
            <SparkLine
              data={pos.price_history}
              width={280}
              height={40}
              color={up >= 0 ? C.green : C.red}
            />
          </View>
        )}
        <View style={styles.posFooter}>
          <Text style={styles.posStrat}>{pos.strategy}</Text>
          <Text style={styles.posStop}>Stop: {pos.stop_loss_price?.toFixed(4)}</Text>
        </View>
      </View>
    );
  };

  const renderTrade = ({ item: t }) => (
    <View style={styles.tradeRow}>
      <Text style={styles.tradeTs}>{fmtTime(t.timestamp)}</Text>
      <Text style={styles.tradeQ} numberOfLines={1}>{t.market_question}</Text>
      <Text style={[styles.tradePnl, { color: pnlColor(t.pnl) }]}>
        {t.pnl >= 0 ? '+' : ''}{fmtUSD(t.pnl, 4)}
      </Text>
    </View>
  );

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Tab selector */}
      <View style={styles.tabs}>
        {['open', 'closed'].map(t => (
          <TouchableOpacity
            key={t}
            style={[styles.tabBtn, tab === t && styles.tabBtnActive]}
            onPress={() => { setTab(t); Haptics.selectionAsync(); }}
          >
            <Text style={[styles.tabTxt, tab === t && styles.tabTxtActive]}>
              {t === 'open' ? `פוזיציות פתוחות (${positions.length})` : `היסטוריית עסקאות (${trades.length})`}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {tab === 'open' ? (
        <FlatList
          data={positions}
          keyExtractor={p => p.token_id}
          renderItem={renderPosition}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); refresh(); }} tintColor={C.green} />}
          ListEmptyComponent={<Text style={styles.empty}>אין פוזיציות פתוחות</Text>}
          contentContainerStyle={{ padding: 16, paddingBottom: 32 }}
        />
      ) : (
        <FlatList
          data={trades}
          keyExtractor={(t, i) => `${t.timestamp}-${i}`}
          renderItem={renderTrade}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); refresh(); }} tintColor={C.green} />}
          ListEmptyComponent={<Text style={styles.empty}>אין עסקאות סגורות</Text>}
          contentContainerStyle={{ padding: 16, paddingBottom: 32 }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },

  tabs:        { flexDirection: 'row', borderBottomWidth: 1, borderColor: C.border },
  tabBtn:      { flex: 1, padding: 14, alignItems: 'center' },
  tabBtnActive:{ borderBottomWidth: 2, borderColor: C.green },
  tabTxt:      { fontSize: 12, color: C.text3 },
  tabTxtActive:{ color: C.green, fontWeight: '600' },

  posCard:   { backgroundColor: C.card, borderRadius: 6, borderWidth: 1, borderColor: C.border, padding: 14, marginBottom: 10 },
  posHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 },
  posQ:      { flex: 1, fontSize: 13, color: C.text, textAlign: 'right', marginLeft: 8 },
  posUp:     { fontSize: 16, fontWeight: '700', fontFamily: 'Courier New' },
  posRow:    { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 },
  posLbl:    { fontSize: 10, color: C.text4, textAlign: 'center', marginBottom: 2 },
  posPx:     { fontSize: 13, color: C.text, fontFamily: 'Courier New', textAlign: 'center' },
  sparkWrap: { alignItems: 'flex-end', marginBottom: 8 },
  posFooter: { flexDirection: 'row', justifyContent: 'space-between' },
  posStrat:  { fontSize: 10, color: C.blue },
  posStop:   { fontSize: 10, color: C.text4, fontFamily: 'Courier New' },

  tradeRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, borderBottomWidth: 1, borderColor: C.border },
  tradeTs:  { fontSize: 11, color: C.text4, fontFamily: 'Courier New', width: 60 },
  tradeQ:   { flex: 1, fontSize: 12, color: C.text2, textAlign: 'right', paddingHorizontal: 8 },
  tradePnl: { fontSize: 13, fontFamily: 'Courier New', width: 70, textAlign: 'right' },

  empty: { color: C.text4, textAlign: 'center', marginTop: 40, fontSize: 14 },
});

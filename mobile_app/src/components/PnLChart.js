import React from 'react';
import { View, Text, StyleSheet, Dimensions } from 'react-native';
import Svg, { Polyline, Defs, LinearGradient, Stop, Rect, Path } from 'react-native-svg';
import { C } from '../theme';

const { width: SCREEN_W } = Dimensions.get('window');
const CHART_W = SCREEN_W - 32;
const CHART_H = 160;
const PAD_L = 4;
const PAD_R = 4;
const PAD_T = 8;
const PAD_B = 8;

export default function PnLChart({ data = [] }) {
  if (data.length < 2) {
    return (
      <View style={styles.empty}>
        <Text style={styles.emptyText}>ממתין לנתונים...</Text>
      </View>
    );
  }

  const values = data.map(d => d.value ?? d);
  const min    = Math.min(...values);
  const max    = Math.max(...values);
  const range  = max - min || 0.01;
  const trend  = values[values.length - 1] - values[0];
  const color  = trend >= 0 ? C.green : C.red;

  const usableW = CHART_W - PAD_L - PAD_R;
  const usableH = CHART_H - PAD_T - PAD_B;

  const pts = values.map((v, i) => ({
    x: PAD_L + (i / (values.length - 1)) * usableW,
    y: PAD_T + usableH - ((v - min) / range) * usableH,
  }));

  const polyPts    = pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const lastPt     = pts[pts.length - 1];
  const lastVal    = values[values.length - 1];

  // Area fill path
  const areaPath =
    `M${pts[0].x},${PAD_T + usableH} ` +
    pts.map(p => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ') +
    ` L${lastPt.x},${PAD_T + usableH} Z`;

  return (
    <View>
      <Svg width={CHART_W} height={CHART_H}>
        <Defs>
          <LinearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0" stopColor={color} stopOpacity="0.20" />
            <Stop offset="1" stopColor={color} stopOpacity="0.00" />
          </LinearGradient>
        </Defs>
        <Path d={areaPath} fill="url(#grad)" />
        <Polyline points={polyPts} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      </Svg>
      <Text style={[styles.lastVal, { color }]}>${lastVal.toFixed(4)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  empty: {
    height: CHART_H,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyText: { color: C.text4, fontSize: 13 },
  lastVal: {
    position: 'absolute',
    right: 8,
    top: 4,
    fontSize: 11,
    fontFamily: 'Courier New',
  },
});

import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
} from 'remotion';
import { AnimatedCounter } from './AnimatedCounter';
import { CaptionOverlay } from './Caption';
import { HookOverlay } from './Hook';
import { KenBurnsClip } from './KenBurnsClip';
import { TransitionEffect, TransitionType } from './Transition';
import { CaptionWord, WealthShockProps } from './types';

// ── Pacing constants ──────────────────────────────────────────────────────────
const HOOK_FRAMES = 90;
const HOOK_CLIP_DUR = 15;
const MAIN_CLIP_DUR = 40;
const TRANSITION_DUR = 6;
const TRANSITION_TYPES: TransitionType[] = [
  'flash',
  'glitch',
  'invert',
  'blur_fade',
  'flash',
  'glitch',
  'zoom_burst',
  'invert',
  'blur_fade',
  'zoom_burst',
];

// ── Segment type ──────────────────────────────────────────────────────────────
interface Segment {
  from: number;
  clipIndex: number;
  punchIn: boolean;
  transType: TransitionType;
  isHook: boolean;
}

// ── Music volume with dynamic ducking ─────────────────────────────────────────
const RAMP = 9;

function makeMusicVolFn(captions: CaptionWord[]) {
  const speechRanges = captions.map((w) => [w.startFrame, w.endFrame] as [number, number]);

  return (frame: number): number => {
    for (const [s, e] of speechRanges) {
      if (frame >= s && frame < e) return 0.15;
      if (frame >= s - RAMP && frame < s)
        return interpolate(frame, [s - RAMP, s], [0.4, 0.15], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
      if (frame >= e && frame < e + RAMP)
        return interpolate(frame, [e, e + RAMP], [0.15, 0.4], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
    }
    return 0.4;
  };
}

// ── Music volume hook component ───────────────────────────────────────────────
const MusicAudio: React.FC<{ src: string; captions: CaptionWord[] }> = ({ src, captions }) => {
  const frame = useCurrentFrame();
  const volFn = React.useMemo(() => makeMusicVolFn(captions), [captions]);
  return (
    <Audio
      src={staticFile(src)}
      volume={volFn(frame)}
      loop
    />
  );
};

// ── Main composition ──────────────────────────────────────────────────────────
export const WealthShock: React.FC<WealthShockProps> = ({
  audioFile,
  musicFile,
  clipFiles,
  captions,
  durationInFrames,
  hookText,
  sfxEvents,
  statCharts,
}) => {
  if (!clipFiles || clipFiles.length === 0) {
    return <AbsoluteFill style={{ backgroundColor: '#000' }} />;
  }

  // ── Build segment schedule ─────────────────────────────────────────────────
  const segments: Segment[] = [];

  // Phase 1: hook clips — instant cuts, no transitions
  let cursor = 0;
  let clipIdx = 0;
  while (cursor < HOOK_FRAMES) {
    segments.push({
      from: cursor,
      clipIndex: clipIdx % clipFiles.length,
      punchIn: false,
      transType: 'flash',
      isHook: true,
    });
    cursor += HOOK_CLIP_DUR;
    clipIdx++;
  }

  // Phase 2: main clips + transitions
  cursor = HOOK_FRAMES;
  let mainClipCount = 0;
  while (cursor < durationInFrames) {
    const transType = TRANSITION_TYPES[mainClipCount % TRANSITION_TYPES.length];
    segments.push({
      from: cursor,
      clipIndex: clipIdx % clipFiles.length,
      punchIn: true,
      transType,
      isHook: false,
    });
    cursor += MAIN_CLIP_DUR + TRANSITION_DUR;
    clipIdx++;
    mainClipCount++;
  }

  return (
    <AbsoluteFill style={{ backgroundColor: '#000000' }}>

      {/* ── Stock video clips ─────────────────────────────────────────────────── */}
      {segments.map(({ from, clipIndex, punchIn, isHook }) => {
        const dur = isHook ? HOOK_CLIP_DUR : MAIN_CLIP_DUR;
        return (
          <Sequence key={`clip-${from}`} from={from} durationInFrames={dur}>
            <KenBurnsClip
              src={staticFile(clipFiles[clipIndex])}
              punchIn={punchIn}
            />
          </Sequence>
        );
      })}

      {/* ── Glitch effect at phase 1 → 2 transition (frame 84–89) ──────────── */}
      <Sequence from={84} durationInFrames={6}>
        <TransitionEffect type="glitch" />
      </Sequence>

      {/* ── Transitions between main clips ───────────────────────────────────── */}
      {segments
        .filter((s) => !s.isHook)
        .map(({ from, transType }) => {
          const transFrom = from + MAIN_CLIP_DUR;
          if (transFrom >= durationInFrames) return null;
          return (
            <Sequence
              key={`trans-${transFrom}`}
              from={transFrom}
              durationInFrames={TRANSITION_DUR}
            >
              <TransitionEffect type={transType} />
            </Sequence>
          );
        })}

      {/* ── Hook overlay (phase 1) ─────────────────────────────────────────────── */}
      <Sequence from={0} durationInFrames={HOOK_FRAMES}>
        <HookOverlay hookText={hookText || 'SHOCKING MONEY FACTS'} />
      </Sequence>

      {/* ── Word-by-word captions (phase 2+) ────────────────────────────────── */}
      <CaptionOverlay captions={captions} />

      {/* ── Stat counters ─────────────────────────────────────────────────────── */}
      {(statCharts || []).map((chart, i) => (
        <Sequence key={`stat-${i}`} from={chart.frame} durationInFrames={60}>
          <AnimatedCounter
            value={chart.value}
            unit={chart.unit}
            label={chart.label}
            startFrame={chart.frame}
            durationFrames={60}
          />
        </Sequence>
      ))}

      {/* ── SFX audio events ──────────────────────────────────────────────────── */}
      {(sfxEvents || []).map((event, i) => (
        <Sequence key={`sfx-${i}-${event.frame}`} from={event.frame} durationInFrames={30}>
          <Audio
            src={staticFile(event.src)}
            startFrom={0}
            volume={event.volume ?? 0.7}
          />
        </Sequence>
      ))}

      {/* ── Voiceover ─────────────────────────────────────────────────────────── */}
      {audioFile ? <Audio src={staticFile(audioFile)} /> : null}

      {/* ── Background music with ducking ─────────────────────────────────────── */}
      {musicFile ? <MusicAudio src={musicFile} captions={captions || []} /> : null}

      {/* ── WealthShock watermark ─────────────────────────────────────────────── */}
      <AbsoluteFill
        style={{
          justifyContent: 'flex-end',
          alignItems: 'flex-end',
          padding: '24px',
          pointerEvents: 'none',
        }}
      >
        <span
          style={{
            color: 'rgba(255,255,255,0.35)',
            fontSize: '32px',
            fontFamily: '"Arial Black", Impact, sans-serif',
            fontWeight: 700,
            letterSpacing: '1px',
          }}
        >
          WealthShock
        </span>
      </AbsoluteFill>

    </AbsoluteFill>
  );
};

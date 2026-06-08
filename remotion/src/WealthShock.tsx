import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Sequence,
  staticFile,
} from 'remotion';
import { CaptionOverlay } from './Caption';
import { KenBurnsClip } from './KenBurnsClip';
import { WealthShockProps } from './types';

const CLIP_FRAMES = 75;   // 2.5 s at 30 fps
const FLASH_FRAMES = 2;   // 2-frame white pop between clips

export const WealthShock: React.FC<WealthShockProps> = ({
  audioFile,
  musicFile,
  clipFiles,
  captions,
  durationInFrames,
}) => {
  if (!clipFiles || clipFiles.length === 0) return <AbsoluteFill style={{ backgroundColor: '#000' }} />;

  // Build clip sequence — no title card, content starts at frame 0
  const segments: { from: number; clipIndex: number }[] = [];
  let cursor = 0;
  let idx = 0;
  while (cursor < durationInFrames) {
    segments.push({ from: cursor, clipIndex: idx % clipFiles.length });
    cursor += CLIP_FRAMES + FLASH_FRAMES;
    idx++;
  }

  return (
    <AbsoluteFill style={{ backgroundColor: '#000000' }}>

      {/* ── Stock video clips with Ken Burns zoom ── */}
      {segments.map(({ from, clipIndex }) => (
        <Sequence key={`clip-${from}`} from={from} durationInFrames={CLIP_FRAMES}>
          <KenBurnsClip src={staticFile(clipFiles[clipIndex])} />
        </Sequence>
      ))}

      {/* ── 2-frame white flash between clips ── */}
      {segments.slice(1).map(({ from }) => (
        <Sequence
          key={`flash-${from}`}
          from={from - FLASH_FRAMES}
          durationInFrames={FLASH_FRAMES}
        >
          <AbsoluteFill style={{ backgroundColor: '#ffffff' }} />
        </Sequence>
      ))}

      {/* ── Word-by-word animated captions ── */}
      <CaptionOverlay captions={captions} />

      {/* ── WealthShock watermark ── */}
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

      {/* ── Voiceover ── */}
      {audioFile && <Audio src={staticFile(audioFile)} />}

      {/* ── Background music at 8% volume ── */}
      {musicFile && <Audio src={staticFile(musicFile)} volume={0.08} loop />}

    </AbsoluteFill>
  );
};

import React from 'react';
import { AbsoluteFill, spring, useCurrentFrame } from 'remotion';
import { CaptionWord } from './types';

const FPS = 30;

interface WordDisplayProps {
  word: CaptionWord;
  localFrame: number;
  durationFrames: number;
}

const WordDisplay: React.FC<WordDisplayProps> = ({ word, localFrame, durationFrames }) => {
  // Spring bounce slam-in: scale 0.5 → 1.0
  const bounceScale = spring({
    fps: FPS,
    frame: localFrame,
    config: { damping: 150, stiffness: 350, mass: 0.5 },
    from: 0.5,
    to: 1.0,
    durationInFrames: Math.min(10, durationFrames),
  });

  const isRed = word.emphasis === 'red';
  const isYellow = word.emphasis === 'yellow';

  // Red: slight jitter translateX
  const jitterX = isRed ? Math.sin(localFrame * 17) * 2 : 0;

  // Yellow: 1.05× scale multiplier at bounce peak
  const yellowMult = isYellow ? 1.05 : 1.0;

  const fontSize = isRed ? 130 : 120;
  const color = isRed ? '#FF3333' : isYellow ? '#FFD700' : '#FFFFFF';

  const finalScale = bounceScale * yellowMult;

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '8px',
        fontSize: `${fontSize}px`,
        fontFamily: 'Impact, "Arial Black", "Helvetica Neue", sans-serif',
        fontWeight: 900,
        color,
        WebkitTextStroke: '8px #000000',
        textShadow: '6px 6px 0 rgba(0,0,0,0.9)',
        transform: `scale(${finalScale}) translateX(${jitterX}px)`,
        transformOrigin: 'center bottom',
        letterSpacing: '2px',
        lineHeight: 1.0,
        whiteSpace: 'nowrap',
      }}
    >
      <span>{word.text.toUpperCase()}</span>
      {word.emoji ? (
        <span style={{ fontSize: `${Math.round(fontSize * 0.85)}px`, WebkitTextStroke: '0px' }}>
          {word.emoji}
        </span>
      ) : null}
    </div>
  );
};

export const CaptionOverlay: React.FC<{ captions: CaptionWord[] }> = ({ captions }) => {
  const frame = useCurrentFrame();

  const active = captions.find((w) => frame >= w.startFrame && frame < w.endFrame);
  if (!active) return null;

  const localFrame = frame - active.startFrame;
  const durationFrames = active.endFrame - active.startFrame;

  return (
    <AbsoluteFill
      style={{
        justifyContent: 'flex-end',
        alignItems: 'center',
        paddingBottom: '260px',
        pointerEvents: 'none',
      }}
    >
      <WordDisplay
        word={active}
        localFrame={localFrame}
        durationFrames={durationFrames}
      />
    </AbsoluteFill>
  );
};

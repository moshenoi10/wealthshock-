import React from 'react';
import { AbsoluteFill, spring, useCurrentFrame } from 'remotion';
import { CaptionWord } from './types';

const FPS = 30;

export const CaptionOverlay: React.FC<{ captions: CaptionWord[] }> = ({ captions }) => {
  const frame = useCurrentFrame();

  const active = captions.find((w) => frame >= w.startFrame && frame < w.endFrame);
  if (!active) return null;

  const localFrame = frame - active.startFrame;
  const durationFrames = active.endFrame - active.startFrame;

  const scale = spring({
    fps: FPS,
    frame: localFrame,
    config: { damping: 200, stiffness: 350, mass: 0.4 },
    from: 0.55,
    to: 1.0,
    durationInFrames: Math.min(7, durationFrames),
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: 'flex-end',
        alignItems: 'center',
        paddingBottom: '280px',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          fontSize: '112px',
          fontWeight: 900,
          color: '#FFFFFF',
          textAlign: 'center',
          fontFamily: '"Arial Black", Impact, "Helvetica Neue", sans-serif',
          WebkitTextStroke: '7px #000000',
          textShadow: '5px 5px 0px rgba(0,0,0,0.85)',
          transform: `scale(${scale})`,
          transformOrigin: 'center bottom',
          letterSpacing: '2px',
          maxWidth: '980px',
          lineHeight: 1.0,
          padding: '0 28px',
        }}
      >
        {active.text.toUpperCase()}
      </div>
    </AbsoluteFill>
  );
};

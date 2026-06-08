import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame } from 'remotion';

const FPS = 30;

interface HookProps {
  hookText: string;
}

export const HookOverlay: React.FC<HookProps> = ({ hookText }) => {
  const frame = useCurrentFrame();

  // Spring slam-in: scale 3.0 → 1.0 with overshoot physics, frames 0–7
  const scaleSpring = spring({
    fps: FPS,
    frame,
    config: { damping: 8, stiffness: 220, mass: 0.6 },
    from: 3.0,
    to: 1.0,
    durationInFrames: 12,
  });

  // Camera shake: sine-wave dampening, frames 2–10
  const shakeAmp = interpolate(frame, [2, 10], [14, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const shakeX = Math.sin(frame * 2.8) * shakeAmp;
  const shakeY = Math.cos(frame * 3.1) * shakeAmp * 0.6;

  // Fade out: frames 80–89
  const opacity = interpolate(frame, [80, 89], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: 'center',
        alignItems: 'center',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          fontSize: '140px',
          fontFamily: '"Arial Black", Impact, "Helvetica Neue", sans-serif',
          fontWeight: 900,
          color: '#FFD700',
          WebkitTextStroke: '8px #000000',
          textShadow: '6px 6px 0 rgba(0,0,0,0.9)',
          textAlign: 'center',
          maxWidth: '980px',
          lineHeight: 1.0,
          padding: '0 24px',
          transform: `scale(${scaleSpring}) translate(${shakeX}px, ${shakeY}px)`,
          transformOrigin: 'center center',
          opacity,
          letterSpacing: '1px',
        }}
      >
        {hookText.toUpperCase()}
      </div>
    </AbsoluteFill>
  );
};

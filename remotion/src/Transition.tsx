import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';

export type TransitionType =
  | 'flash'
  | 'glitch'
  | 'invert'
  | 'blur_fade'
  | 'zoom_burst';

interface TransitionEffectProps {
  type: TransitionType;
}

// ── Flash ──────────────────────────────────────────────────────────────────────
const FlashTransition: React.FC<{ duration: number }> = ({ duration }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, duration - 1], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill
      style={{ backgroundColor: '#ffffff', opacity, pointerEvents: 'none' }}
    />
  );
};

// ── Glitch ─────────────────────────────────────────────────────────────────────
const GlitchTransition: React.FC<{ duration: number }> = ({ duration }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, duration - 1], [0.85, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const hueRot = (frame * 47) % 360;

  // 8 horizontal scan bars
  const bars = Array.from({ length: 8 }, (_, i) => {
    const offsetX = Math.sin(frame * 2.3 + i * 1.7) * 18;
    const barH = 100 / 8; // percent
    return (
      <div
        key={i}
        style={{
          position: 'absolute',
          top: `${i * barH}%`,
          left: `${offsetX}px`,
          right: 0,
          height: `${barH}%`,
          backgroundColor: i % 2 === 0 ? 'rgba(255,0,0,0.15)' : 'rgba(0,0,255,0.10)',
          mixBlendMode: 'screen',
        }}
      />
    );
  });

  return (
    <AbsoluteFill style={{ opacity, pointerEvents: 'none', filter: `hue-rotate(${hueRot}deg)` }}>
      {/* Red channel offset */}
      <AbsoluteFill
        style={{
          backgroundColor: 'rgba(255,0,0,0.35)',
          transform: 'translateX(12px)',
          mixBlendMode: 'screen',
        }}
      />
      {/* Blue channel offset */}
      <AbsoluteFill
        style={{
          backgroundColor: 'rgba(0,0,255,0.35)',
          transform: 'translateX(-12px)',
          mixBlendMode: 'screen',
        }}
      />
      {/* Scan bars */}
      {bars}
    </AbsoluteFill>
  );
};

// ── Invert ─────────────────────────────────────────────────────────────────────
const InvertTransition: React.FC = () => {
  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#ffffff',
        mixBlendMode: 'difference',
        opacity: 1,
        pointerEvents: 'none',
      }}
    />
  );
};

// ── Blur Fade ──────────────────────────────────────────────────────────────────
const BlurFadeTransition: React.FC<{ duration: number }> = ({ duration }) => {
  const frame = useCurrentFrame();
  const mid = duration / 2;

  // Fade in then out
  const opacity =
    frame <= mid
      ? interpolate(frame, [0, mid], [0, 0.85], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        })
      : interpolate(frame, [mid, duration - 1], [0.85, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });

  return (
    <AbsoluteFill
      style={{
        background:
          'radial-gradient(ellipse at center, transparent 0%, rgba(0,0,0,0.95) 100%)',
        opacity,
        pointerEvents: 'none',
      }}
    />
  );
};

// ── Zoom Burst ─────────────────────────────────────────────────────────────────
const ZoomBurstTransition: React.FC<{ duration: number }> = ({ duration }) => {
  const frame = useCurrentFrame();

  // Frame 0: pure white flash
  if (frame === 0) {
    return <AbsoluteFill style={{ backgroundColor: '#ffffff', pointerEvents: 'none' }} />;
  }

  // Scale 1.0 → 1.05 → 1.0 over remaining frames
  const scale = interpolate(
    frame,
    [0, Math.floor(duration / 2), duration - 1],
    [1.0, 1.05, 1.0],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }
  );

  const opacity = interpolate(frame, [0, duration - 1], [0.7, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        transform: `scale(${scale})`,
        transformOrigin: 'center center',
        backgroundColor: '#ffffff',
        opacity,
        pointerEvents: 'none',
      }}
    />
  );
};

// ── Main TransitionEffect ──────────────────────────────────────────────────────
export const TransitionEffect: React.FC<TransitionEffectProps> = ({ type }) => {
  const { durationInFrames } = useVideoConfig();

  switch (type) {
    case 'flash':
      return <FlashTransition duration={durationInFrames} />;
    case 'glitch':
      return <GlitchTransition duration={durationInFrames} />;
    case 'invert':
      return <InvertTransition />;
    case 'blur_fade':
      return <BlurFadeTransition duration={durationInFrames} />;
    case 'zoom_burst':
      return <ZoomBurstTransition duration={durationInFrames} />;
    default:
      return <FlashTransition duration={durationInFrames} />;
  }
};

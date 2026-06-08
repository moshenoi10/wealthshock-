import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame } from 'remotion';

interface AnimatedCounterProps {
  value: number;
  unit: string;
  label: string;
  startFrame: number;
  durationFrames?: number;
}

export const AnimatedCounter: React.FC<AnimatedCounterProps> = ({
  value,
  unit,
  label,
  // startFrame is used externally for Sequence placement; inside the Sequence
  // useCurrentFrame() already starts at 0, so we don't need it here.
  durationFrames = 60,
}) => {
  const frame = useCurrentFrame();
  const totalDur = durationFrames;
  const ANIM_FRAMES = 45;
  const FADE_IN_END = 10;
  const FADE_OUT_START = totalDur - 10;

  // Count up from 0 to value over 45 frames
  const displayValue = interpolate(frame, [0, ANIM_FRAMES], [0, value], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Fade in
  const opacityIn = interpolate(frame, [0, FADE_IN_END], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Fade out
  const opacityOut = interpolate(frame, [FADE_OUT_START, totalDur - 1], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const opacity = frame < FADE_OUT_START ? opacityIn : opacityOut;

  // Format display number
  const rounded = Math.round(displayValue);
  const formatted =
    rounded >= 1_000_000
      ? `${(rounded / 1_000_000).toFixed(1)}M`
      : rounded >= 1_000
      ? `${(rounded / 1_000).toFixed(1)}K`
      : rounded.toLocaleString();

  const displayText =
    unit === '$' ? `$${formatted}` : unit === '%' ? `${formatted}%` : `${formatted}${unit}`;

  return (
    <AbsoluteFill
      style={{
        justifyContent: 'flex-start',
        alignItems: 'flex-end',
        flexDirection: 'column',
        paddingTop: '40px',
        paddingRight: '40px',
        pointerEvents: 'none',
        opacity,
      }}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-end',
          backgroundColor: 'rgba(0,0,0,0.65)',
          borderRadius: '12px',
          padding: '12px 20px',
          borderLeft: '4px solid #FFD700',
        }}
      >
        <span
          style={{
            fontSize: '72px',
            fontFamily: '"Arial Black", Impact, "Helvetica Neue", sans-serif',
            fontWeight: 900,
            color: '#FFFFFF',
            WebkitTextStroke: '4px #000000',
            textShadow: '4px 4px 0 rgba(0,0,0,0.9)',
            lineHeight: 1.0,
          }}
        >
          {displayText}
        </span>
        {label ? (
          <span
            style={{
              fontSize: '28px',
              fontFamily: '"Arial Black", Impact, "Helvetica Neue", sans-serif',
              fontWeight: 700,
              color: '#FFD700',
              WebkitTextStroke: '1px #000000',
              lineHeight: 1.2,
              marginTop: '4px',
              maxWidth: '340px',
              textAlign: 'right',
            }}
          >
            {label.toUpperCase()}
          </span>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

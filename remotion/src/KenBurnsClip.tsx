import React from 'react';
import {
  AbsoluteFill,
  OffthreadVideo,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

interface KenBurnsClipProps {
  src: string;
  punchIn?: boolean;
}

const FPS = 30;

export const KenBurnsClip: React.FC<KenBurnsClipProps> = ({ src, punchIn = false }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  // Ken Burns: slow zoom 1.0 → 1.08 over full clip
  const kbScale = interpolate(frame, [0, durationInFrames], [1.0, 1.08], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Punch-in: spring from scale 1.14 → 1.0 in first 8 frames
  const entryScale = punchIn
    ? spring({
        fps: FPS,
        frame,
        config: { damping: 400, stiffness: 500, mass: 0.8 },
        from: 1.14,
        to: 1.0,
        durationInFrames: 8,
      })
    : 1.0;

  // Final scale: when punching in, use whichever is larger
  const finalScale = punchIn ? Math.max(kbScale, entryScale) : kbScale;

  return (
    <AbsoluteFill>
      <OffthreadVideo
        src={src}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform: `scale(${finalScale})`,
          transformOrigin: 'center center',
          filter: 'contrast(1.2) saturate(1.4) brightness(1.02)',
        }}
      />
      {/* Gradient overlay — improves caption legibility */}
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(to bottom, rgba(0,0,0,0.05) 0%, rgba(0,0,0,0.2) 55%, rgba(0,0,0,0.7) 100%)',
        }}
      />
    </AbsoluteFill>
  );
};

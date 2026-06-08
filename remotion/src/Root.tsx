import React from 'react';
import { Composition } from 'remotion';
import { WealthShock } from './WealthShock';
import { WealthShockProps } from './types';

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="WealthShock"
      component={WealthShock}
      durationInFrames={1800}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={
        {
          audioFile: '',
          musicFile: '',
          clipFiles: [],
          captions: [],
          durationInFrames: 1800,
        } as WealthShockProps
      }
      calculateMetadata={async ({ props }) => {
        const p = props as WealthShockProps;
        return {
          durationInFrames: p.durationInFrames && p.durationInFrames > 0 ? p.durationInFrames : 1800,
        };
      }}
    />
  );
};

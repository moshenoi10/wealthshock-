export type CaptionWord = {
  text: string;
  startFrame: number;
  endFrame: number;
};

export type WealthShockProps = {
  audioFile: string;
  musicFile: string;
  clipFiles: string[];
  captions: CaptionWord[];
  durationInFrames: number;
};

export type EmphasisType = 'yellow' | 'red' | null;

export type CaptionWord = {
  text: string;
  startFrame: number;
  endFrame: number;
  emphasis: EmphasisType;
  emoji: string;
};

export type SFXEvent = {
  frame: number;
  src: string; // staticFile path, e.g. 'sfx/whoosh.wav'
  volume?: number;
};

export type StatChart = {
  frame: number;
  value: number;
  unit: string; // '%', '$', or ''
  label: string;
};

export type WealthShockProps = {
  audioFile: string;
  musicFile: string;
  clipFiles: string[];
  captions: CaptionWord[];
  durationInFrames: number;
  hookText: string;
  sfxEvents: SFXEvent[];
  statCharts: StatChart[];
};

'use strict';

/**
 * Remotion headless renderer for WealthShock.
 * Called by video_processor.py after assets are staged in public/.
 *
 * Reads:  ../remotion_props.json
 * Writes: ../final_video.mp4
 */

const { bundle }                          = require('@remotion/bundler');
const { renderMedia, selectComposition }  = require('@remotion/renderer');
const path                                = require('path');
const fs                                  = require('fs');

async function main() {
  const propsPath = path.join(__dirname, '..', 'remotion_props.json');
  if (!fs.existsSync(propsPath)) {
    console.error('[remotion] ERROR: remotion_props.json not found at', propsPath);
    process.exit(1);
  }
  const props = JSON.parse(fs.readFileSync(propsPath, 'utf-8'));
  console.log(
    `[remotion] Props: ${props.clipFiles ? props.clipFiles.length : 0} clips,` +
    ` ${props.captions ? props.captions.length : 0} caption words,` +
    ` ${props.durationInFrames} frames`
  );

  const entryPoint = path.join(__dirname, 'src', 'index.ts');
  const publicDir  = path.join(__dirname, 'public');

  // ── Bundle ────────────────────────────────────────────────────────────────
  console.log('[remotion] Bundling project...');
  const bundleLocation = await bundle({
    entryPoint,
    publicDir,
    onProgress: (p) => process.stdout.write(`\r[remotion] Bundle ${p}%   `),
  });
  process.stdout.write('\n');
  console.log('[remotion] Bundle complete.');

  // ── Select composition (applies calculateMetadata with actual props) ─────
  const composition = await selectComposition({
    serveUrl: bundleLocation,
    id: 'WealthShock',
    inputProps: props,
  });
  console.log(`[remotion] Composition: ${composition.durationInFrames} frames @ ${composition.fps} fps`);

  // ── Render ────────────────────────────────────────────────────────────────
  const outputPath = path.join(__dirname, '..', 'final_video.mp4');
  console.log(`[remotion] Rendering to ${outputPath} ...`);

  await renderMedia({
    composition,
    serveUrl: bundleLocation,
    codec: 'h264',
    outputLocation: outputPath,
    inputProps: props,
    concurrency: 4,
    crf: 26,
    overwrite: true,
    onProgress: ({ progress, renderedFrames, totalFrames }) => {
      process.stdout.write(
        `\r[remotion] ${(progress * 100).toFixed(1)}%  (${renderedFrames}/${totalFrames} frames)`
      );
    },
  });

  process.stdout.write('\n');

  if (!fs.existsSync(outputPath)) {
    console.error('[remotion] ERROR: output file not created');
    process.exit(1);
  }

  const sizeMB = (fs.statSync(outputPath).size / 1024 / 1024).toFixed(1);
  console.log(`[remotion] SUCCESS: final_video.mp4 (${sizeMB} MB)`);
}

main().catch((err) => {
  console.error('[remotion] FATAL:', err);
  process.exit(1);
});

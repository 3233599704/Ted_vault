const DEFAULT_SAMPLE_RATE = 24_000;

export function pcmToWav(pcm, sampleRate = DEFAULT_SAMPLE_RATE) {
  const pcmBuffer = Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  const wav = Buffer.alloc(44 + pcmBuffer.length);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write("WAVE", 8);
  wav.write("fmt ", 12);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24);
  wav.writeUInt32LE(sampleRate * 2, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(pcmBuffer.length, 40);
  pcmBuffer.copy(wav, 44);
  return wav;
}

export async function silkToWav(silkBuffer, sampleRate = DEFAULT_SAMPLE_RATE) {
  const { decode } = await import("silk-wasm");
  const decoded = await decode(silkBuffer, sampleRate);
  return pcmToWav(decoded.data, sampleRate);
}

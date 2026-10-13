export function wavBlob(parts: Float32Array[], sampleRate: number) {
  const length = parts.reduce((total, part) => total + part.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) => Array.from(value).forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const part of parts) for (let index=0; index<part.length; index++) {
    const value=part[index];
    const clamped = Math.max(-1, Math.min(1, value));
    view.setInt16(offset, Math.round(clamped * (clamped < 0 ? 32768 : 32767)), true);
    offset += 2;
  }
  return new Blob([buffer], {type: "audio/wav"});
}

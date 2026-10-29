import { vi } from 'vitest';

export const pngHeader = [137, 80, 78, 71, 13, 10, 26, 10];

/** jsdom File has no Blob.arrayBuffer; preserve its bounded slice semantics. */
export function imageFile(name = 'image.png', header = pngHeader) {
  const bytes = new Uint8Array(header);
  const file = new File([bytes], name, { type: 'application/octet-stream' });
  Object.defineProperty(file, 'webkitRelativePath', {
    value: `images/nested/${name}`,
  });
  const slice = file.slice.bind(file);
  vi.spyOn(file, 'slice').mockImplementation((start, end, type) => {
    const blob = slice(start, end, type);
    Object.defineProperty(blob, 'arrayBuffer', {
      configurable: true,
      value: () => Promise.resolve(bytes.slice(start, end).buffer),
    });
    return blob;
  });
  return file;
}

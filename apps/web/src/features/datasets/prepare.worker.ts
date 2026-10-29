/** Hash one bounded original at a time without blocking the dashboard. */
export {};
interface Preparation {
  files: File[];
  paths: string[];
  displayName: string;
  excluded: number;
}
const hex = (bytes: ArrayBuffer) =>
  [...new Uint8Array(bytes)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');
self.onmessage = async (event: MessageEvent<Preparation>) => {
  try {
    const { files, paths, displayName, excluded } = event.data;
    const entries = [];
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      if (!file) throw new Error('Missing file');
      const sha256 = hex(
        await crypto.subtle.digest('SHA-256', await file.arrayBuffer()),
      );
      entries.push({
        part: `file-${index}`,
        relative_path: paths[index],
        size_bytes: file.size,
        sha256,
      });
      self.postMessage({ kind: 'progress', count: index + 1 });
    }
    const metadata = JSON.stringify({
      schema_version: 1,
      display_name: displayName,
      selection: {
        mode: excluded ? 'explicit-subset' : 'whole-folder',
        excluded_count: excluded,
      },
      files: entries,
    });
    const digest = hex(
      await crypto.subtle.digest('SHA-256', new TextEncoder().encode(metadata)),
    );
    self.postMessage({ kind: 'complete', metadata, digest });
  } catch {
    self.postMessage({ kind: 'error' });
  }
};

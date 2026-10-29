export interface ReviewFile {
  file: File;
  path: string;
  included: boolean;
  problem: string;
  unsupported: boolean;
}

const fileTypes: Record<string, string> = {
  pdf: 'PDF document',
  txt: 'Text file',
  zip: 'ZIP archive',
  mp4: 'Video',
  mov: 'Video',
  exe: 'Executable',
  db: 'Database or sidecar file',
};

/** Header inspection is a folder preflight; only the server decodes images. */
export async function reviewFile(
  file: File,
  maxBytes: number,
): Promise<ReviewFile> {
  const path = file.webkitRelativePath
    ? file.webkitRelativePath.split('/').slice(1).join('/')
    : file.name;
  const extension = file.name.split('.').at(-1)?.toLowerCase() ?? '';
  let problem = '';
  let unsupported = false;
  if (!['png', 'jpg', 'jpeg'].includes(extension)) {
    problem = fileTypes[extension] ?? 'Unsupported file type';
    unsupported = true;
  } else {
    try {
      const bytes = new Uint8Array(await file.slice(0, 8).arrayBuffer());
      const png = [137, 80, 78, 71, 13, 10, 26, 10].every(
        (value, index) => bytes[index] === value,
      );
      const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
      unsupported = extension === 'png' ? !png : !jpeg;
      if (unsupported)
        problem =
          'File signature does not match a PNG/JPEG image with this extension';
    } catch {
      unsupported = true;
      problem = 'File cannot be read';
    }
  }
  if (!unsupported && (!file.size || file.size > maxBytes))
    problem = 'File size exceeds import limits';
  return { file, path, included: true, problem, unsupported };
}

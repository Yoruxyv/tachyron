const paths = {
  overview: 'M3 10 12 3l9 7v11h-6v-7H9v7H3Z',
  experiments: 'M4 4h16v16H4ZM8 9h8M8 14h5',
  compare: 'M4 20V10m5 10V4m6 16v-7m5 7V7',
  history: 'M4 4h16v16H4Zm4 4h8M8 12h8M8 16h5',
  api: 'm12 2 9 5v10l-9 5-9-5V7Zm-4 8 4 3 4-3m-4 3v5',
  time: 'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  speed: 'm14 2-9 11h7l-2 9 9-12h-7Z',
  cpu: 'M5 5h14v14H5ZM9 1v4m6-4v4M9 19v4m6-4v4M1 9h4m-4 6h4m14-6h4m-4 6h4',
  efficiency: 'm8 12 3 3 5-6M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
};
export default function Icon({ name }: { readonly name: keyof typeof paths }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}

// 线性图标集（无外部依赖，避免为几个图标引入整个图标库）
interface IconProps {
  name: string
  size?: number
  className?: string
}

const PATHS: Record<string, string> = {
  target: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0M12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0 -10 0M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0',
  doc: 'M14 3v5h5M17 21H7a2 2 0 0 1 -2 -2V5a2 2 0 0 1 2 -2h7l5 5v11a2 2 0 0 1 -2 2zM9 13h6M9 17h4',
  chat: 'M21 12a8 8 0 0 1 -8 8H8l-5 3V12a8 8 0 0 1 8 -8h2a8 8 0 0 1 8 8z',
  chart: 'M3 3v18h18M8 16V10M13 16V6M18 16v-4',
  truck: 'M2 7h11v10H2zM13 10h4l3 3v4h-7zM6 19a2 2 0 1 0 4 0a2 2 0 1 0 -4 0M16 19a2 2 0 1 0 4 0a2 2 0 1 0 -4 0',
  shield: 'M12 3l8 4v5c0 5 -3.5 8 -8 9c-4.5 -1 -8 -4 -8 -9V7zM9 12l2 2l4 -4',
  spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8 -5.2L5 10l5.2 -1.8zM19 16l.9 2.1L22 19l-2.1 .9L19 22l-.9 -2.1L16 19l2.1 -.9z',
  bolt: 'M13 3L4 14h7l-1 7l9 -11h-7z',
  copy: 'M9 9h10v10H9zM5 15V5h10',
  check: 'M5 13l4 4L19 7',
  alert: 'M12 9v4M12 17h.01M10.3 4.3L2.6 17.6A2 2 0 0 0 4.3 20.6h15.4a2 2 0 0 0 1.7 -3L13.7 4.3a2 2 0 0 0 -3.4 0z',
  book: 'M4 5a2 2 0 0 1 2 -2h12v18H6a2 2 0 0 1 -2 -2zM8 7h7M8 11h7',
  refresh: 'M20 11A8 8 0 0 0 6.3 6.3L4 8.5M4 5v3.5h3.5M4 13a8 8 0 0 0 13.7 4.7L20 15.5M20 19v-3.5h-3.5',
  arrow: 'M5 12h14M13 6l6 6l-6 6',
}

export function Icon({ name, size = 18, className }: IconProps) {
  const d = PATHS[name]
  if (!d) return null
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  )
}

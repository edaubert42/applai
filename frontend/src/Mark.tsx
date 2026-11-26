/** The ApplAI mark: an A in a rounded square. */
export default function Mark({ className = "size-8" }: { className?: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 32 32" className={className}>
      <rect width="32" height="32" rx="10" className="fill-primary" />
      <path
        d="M10 23 16 9l6 14M12.5 18h7"
        fill="none"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="stroke-on-primary"
      />
    </svg>
  );
}

/**
 * A text in paragraphs, as a cover letter reads: each block between blank lines is its own
 * paragraph, spaced, at about 70 characters a line. A single line break inside a block (a
 * sign-off and its name) stays.
 */
export default function Paragraphs({ text, className = "" }: { text: string; className?: string }) {
  const paragraphs = text.split(/\n\s*\n/).filter((paragraph) => paragraph.trim());
  return (
    <div className={`max-w-[70ch] space-y-3 ${className}`}>
      {paragraphs.map((paragraph, index) => (
        <p key={index} className="whitespace-pre-line [overflow-wrap:anywhere]">
          {paragraph.trim()}
        </p>
      ))}
    </div>
  );
}

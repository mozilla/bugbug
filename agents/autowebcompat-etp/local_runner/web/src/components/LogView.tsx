import { useLayoutEffect, useRef } from "react";

function lineClass(line: string): string {
  if (line.startsWith("[agent]")) return "agent";
  if (line.startsWith("[done]") || line.startsWith("=====")) return "done";
  if (/ERROR|Traceback|\[tool←ERROR\]| - ERROR - /.test(line)) return "err";
  if (line.startsWith("[agent→tool]") || line.startsWith("[tool←"))
    return "tool";
  return "";
}

/** Agent output, auto-scrolling while the user is at the bottom. */
export function LogView({ lines }: { lines: readonly string[] }) {
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <pre
      className="log"
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
    >
      {lines.map((line, i) => (
        <span key={i} className={lineClass(line)}>
          {line}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

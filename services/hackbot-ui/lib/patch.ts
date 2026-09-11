// Parse the `changes/changes.patch` artifact into per-file diffs for preview.
//
// The artifact is an mbox from `git format-patch`, so it carries mail headers
// and a `-- \n<git version>` signature around each commit's diff. Hunk bodies
// are consumed by the line counts in their `@@` header rather than by looking
// for a terminator, which is what keeps that signature (and a `From <sha>` line
// opening the next commit) from being read as a deleted line.

export type DiffLineKind = "add" | "del" | "hunk" | "ctx";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

export type PatchFileStatus = "added" | "deleted" | "renamed" | "modified";

export interface PatchFile {
  path: string;
  oldPath: string | null;
  status: PatchFileStatus;
  added: number;
  removed: number;
  binary: boolean;
  lines: DiffLine[];
}

const FILE_RE = /^diff --git a\/(.+) b\/(.+)$/;
const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

export function parsePatch(text: string): PatchFile[] {
  const lines = text.split("\n");
  const files: PatchFile[] = [];
  let i = 0;

  while (i < lines.length) {
    const match = FILE_RE.exec(lines[i]);
    if (!match) {
      i++;
      continue;
    }
    const [, oldPath, newPath] = match;
    const file: PatchFile = {
      path: newPath,
      oldPath: null,
      status: "modified",
      added: 0,
      removed: 0,
      binary: false,
      lines: [],
    };
    files.push(file);
    i++;

    // File header: everything up to the first hunk. `--- a/x` and `+++ b/x`
    // live here, which is why headers are skipped rather than rendered.
    for (; i < lines.length; i++) {
      const line = lines[i];
      if (line.startsWith("@@") || FILE_RE.test(line)) break;
      if (line.startsWith("new file mode")) file.status = "added";
      else if (line.startsWith("deleted file mode")) {
        file.status = "deleted";
        file.path = oldPath;
      } else if (line.startsWith("rename from ")) {
        file.status = "renamed";
        file.oldPath = line.slice("rename from ".length);
      } else if (line.startsWith("GIT binary patch")) {
        file.binary = true;
        i++;
        break;
      }
    }
    if (file.binary) continue;

    while (i < lines.length) {
      const hunk = HUNK_RE.exec(lines[i]);
      if (!hunk) break;
      file.lines.push({ kind: "hunk", text: lines[i] });
      i++;

      let oldLeft = hunk[2] === undefined ? 1 : Number(hunk[2]);
      let newLeft = hunk[4] === undefined ? 1 : Number(hunk[4]);
      while (i < lines.length && (oldLeft > 0 || newLeft > 0)) {
        const line = lines[i];
        i++;
        if (line.startsWith("\\")) continue; // "\ No newline at end of file"
        if (line.startsWith("+")) {
          file.lines.push({ kind: "add", text: line });
          file.added++;
          newLeft--;
        } else if (line.startsWith("-")) {
          file.lines.push({ kind: "del", text: line });
          file.removed++;
          oldLeft--;
        } else {
          file.lines.push({ kind: "ctx", text: line });
          oldLeft--;
          newLeft--;
        }
      }
    }
  }

  return files;
}

// The sibling `changes/changes.json` artifact: what the runtime recorded about
// the commits behind the patch (hackbot_runtime.changes.collect).
export interface PatchCommit {
  sha: string;
  author_name: string;
  authored_date: string;
  subject: string;
  body: string;
}

export interface ChangesMeta {
  base_commit: string;
  wrapped_uncommitted: boolean;
  commits: PatchCommit[];
}

// The commits worth showing as the patch's description. When the agent left work
// uncommitted the runtime squashes the remainder into a trailing placeholder
// commit ("Uncommitted agent changes"), which says nothing about the change, so
// it is dropped rather than displayed as a message.
export function describingCommits(meta: ChangesMeta | null): PatchCommit[] {
  if (!meta) return [];
  const commits = meta.commits ?? [];
  return meta.wrapped_uncommitted ? commits.slice(0, -1) : commits;
}

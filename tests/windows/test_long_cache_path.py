r"""GREEN regression guard — index succeeds when the project DB full path exceeds
Windows MAX_PATH(260).

Before the fix landed on fix/win-longpath-index-crash, the C compat layer passed
UTF-8 paths straight to Win32 file APIs (FindFirstFileW/_wfopen/_wmkdir/...) and
to sqlite3_open_v2 without any \\?\ extended-length normalization. Once
<CBM_CACHE_DIR>/<slug>.db exceeded 260 characters, those APIs failed and the
index worker crashed/errored instead of producing a graph. The fix normalizes
Windows-side paths in src/foundation/compat_fs.c, src/foundation/platform.c, and
src/store/store.c to \\?\-prefixed extended-length paths before they reach
Win32/sqlite, bypassing the legacy MAX_PATH limit.

This guard drives a real codebase-memory-mcp(.exe) over stdio with CBM_CACHE_DIR
pointed at a deeply nested directory so the resulting <slug>.db path is well
past 260 characters, then indexes a small TypeScript fixture and checks that:
  - index_repository reports no error,
  - list_projects returns the project with nodes > 0,
  - query_graph finds at least one Function/Class/Method definition.

On Linux/macOS this is a no-op guard (long paths fit in PATH_MAX and the POSIX
compat_fs.c path is a plain strdup), so the test simply passes there too.

Exit code: 0 == invariant holds (green), 1 == invariant violated (regression),
2 == environment/setup error.

Usage:
    python test_long_cache_path.py <path-to-codebase-memory-mcp[.exe]>
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_stdio import McpServer  # noqa: E402

MATH_TS = (
    "export function add(a: number, b: number): number { return a + b; }\n"
    "export function mul(a: number, b: number): number { return add(a, a); }\n"
    "export class Calc {\n"
    "  total: number = 0;\n"
    "  push(x: number): void { this.total = add(this.total, x); }\n"
    "}\n"
)
MAIN_TS = (
    'import { add, mul, Calc } from "./math";\n'
    "function run(): number {\n"
    "  const c = new Calc();\n"
    "  c.push(add(1, 2));\n"
    "  return mul(3, 4);\n"
    "}\n"
    "run();\n"
)


def _mk(p):
    """mkdir -p that survives MAX_PATH on Windows via the \\?\\ prefix.

    CBM_CACHE_DIR itself is handed to the binary WITHOUT this prefix — the
    point of the test is that the binary's own path handling (not this
    harness) tolerates the long path.
    """
    if os.name == "nt":
        os.makedirs("\\\\?\\" + os.path.abspath(p), exist_ok=True)
    else:
        os.makedirs(p, exist_ok=True)


def build_long_cache_dir(work):
    """Nest directories under `work` until the cache path is comfortably past
    260 characters, so `<cache>/<slug>.db` is guaranteed to exceed MAX_PATH
    regardless of how long the OS temp-dir prefix happens to be."""
    cache = work
    segment = "d" * 40
    while len(cache) < 300:
        cache = os.path.join(cache, segment)
        _mk(cache)
    return cache


def make_fixture(root):
    src = os.path.join(root, "src")
    os.makedirs(src, exist_ok=True)
    for name, text in (("math.ts", MATH_TS), ("main.ts", MAIN_TS)):
        with open(os.path.join(src, name), "wb") as f:
            f.write(text.encode("utf-8"))


def main():
    if len(sys.argv) < 2:
        print("usage: python test_long_cache_path.py <binary>")
        return 2
    binary = os.path.abspath(sys.argv[1])
    if not os.path.exists(binary):
        print("FAIL: binary not found: %s" % binary)
        return 2

    work = tempfile.mkdtemp(prefix="cbm_win_longpath_")
    try:
        cache = build_long_cache_dir(work)
        print("cache dir length: %d" % len(cache))
        if len(cache) < 260:
            print("SETUP FAIL: could not construct a cache path >= 260 chars: %d"
                  % len(cache))
            return 2

        repo = os.path.join(work, "repo")
        make_fixture(repo)

        with McpServer(binary, cache_dir=cache) as s:
            s.initialize()
            resp = s.call_tool("index_repository", {"repo_path": repo}, timeout=180)
            idx_txt, err = s.tool_text(resp)
            if err:
                print("RED: index_repository error: %r" % err)
                print(s.stderr_text())
                return 1

            lp = s.call_tool("list_projects", {}, timeout=60)
            lp_txt, lp_err = s.tool_text(lp)
            if lp_err:
                print("RED: list_projects error: %r" % lp_err)
                return 1
            projects = json.loads(lp_txt).get("projects") or []
            if not projects:
                # Diagnostics: index reports success but nothing was persisted to
                # the long cache path — surface the index response, server stderr,
                # and whether any .db actually landed under the cache dir.
                print("RED: no project listed after index")
                print("index response text: %r" % (idx_txt or "")[:800])
                print("server stderr:\n%s" % s.stderr_text())
                dbs = []
                walk_root = ("\\\\?\\" + cache) if os.name == "nt" else cache
                for dirpath, _dirs, files in os.walk(walk_root):
                    for fn in files:
                        if fn.endswith(".db"):
                            dbs.append(os.path.join(dirpath, fn))
                print("db files found under cache dir: %r" % dbs)
                return 1
            p = projects[0]
            nodes = p.get("nodes") or 0
            print("project=%r nodes=%s edges=%s" % (p.get("name"), nodes, p.get("edges")))
            if nodes <= 0:
                print("RED: indexed project has zero nodes")
                return 1

            defs = 0
            for label in ("Function", "Class", "Method"):
                q = "MATCH (n:%s) RETURN count(n)" % label
                r = s.call_tool("query_graph", {"query": q, "project": p.get("name")},
                                timeout=60)
                t, q_err = s.tool_text(r)
                if q_err:
                    print("RED: query_graph error for %s: %r" % (label, q_err))
                    return 1
                rows = json.loads(t).get("rows") or []
                if rows and rows[0]:
                    defs += int(rows[0][0])
            print("definition_nodes=%d" % defs)
            if defs < 1:
                print("RED: no Function/Class/Method definitions found — long "
                      "cache path likely broke read or write access")
                return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\nGREEN: index succeeded and definitions were found with a cache "
          "path past MAX_PATH.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Single memo MCP server (spec §§4,7,24): 8 tools + bootstrap + memo://summary.

Primary transport: Streamable HTTP (container). Stdio is a fallback.
Import rule: server -> {memory, skills, summary, maintenance} — the ONLY
cross-domain join here is bootstrap text (read-only).
"""
# Load-bearing re-export: the console-script entry point is
# `memo = "memo.server:main"` (pyproject.toml) and the Dockerfile CMD runs
# `memo --db ... --http`, so the attribute lookup needs `main` on the package.
# Keep this ONE name; everything else imports from its defining module directly.
from memo.server.cli import main

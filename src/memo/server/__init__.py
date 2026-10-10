"""Single memo MCP server (spec §§4,7,24): 8 tools + bootstrap + memo://summary.

Primary transport: Streamable HTTP (container). Stdio is a fallback.
Import rule: server -> {memory, skills, summary, maintenance} — the ONLY
cross-domain join here is bootstrap text (read-only).
"""
from memo.server.bootstrap import bootstrap_text, stats_line
from memo.server.cli import (
    backup_database,
    build_parser,
    health_probe,
    main,
    main_http,
    refuse_if_locked,
)
from memo.server.core import build_server, dump_tool_names, server_connect
from memo.server.descriptions import DESCRIPTIONS, load_descriptions
from memo.server.http import BearerAuth, build_http_app
from memo.server.operations import (
    do_memory_delete,
    do_memory_save,
    do_memory_search,
    do_skill_get,
    do_skill_install,
    do_skill_put,
    do_skill_sync,
    do_skill_update,
    sync_call_locked,
)
from memo.server.shared import (
    ALL_TOOLS,
    MEMORY_TOOLS,
    SKILL_TOOLS,
    VERSION,
    env_flag,
    internal_error,
    log_operation,
    resolve_domains,
    startup_line,
)

__all__ = [
    "ALL_TOOLS",
    "DESCRIPTIONS",
    "MEMORY_TOOLS",
    "SKILL_TOOLS",
    "VERSION",
    "BearerAuth",
    "backup_database",
    "bootstrap_text",
    "build_http_app",
    "build_parser",
    "build_server",
    "do_memory_delete",
    "do_memory_save",
    "do_memory_search",
    "do_skill_get",
    "do_skill_install",
    "do_skill_put",
    "do_skill_sync",
    "do_skill_update",
    "dump_tool_names",
    "env_flag",
    "health_probe",
    "internal_error",
    "load_descriptions",
    "log_operation",
    "main",
    "main_http",
    "refuse_if_locked",
    "resolve_domains",
    "server_connect",
    "startup_line",
    "stats_line",
    "sync_call_locked",
]

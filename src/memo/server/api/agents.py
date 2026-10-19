from __future__ import annotations

import json

from starlette.concurrency import run_in_threadpool

from memo import db as memo_db
from memo.models.orm import Agent as OrmAgent, AgentState as OrmAgentState

from .models import AgentList


def build_agents(conn) -> dict:
    with memo_db.orm_ctx(conn):
        agent_rows = list(OrmAgent.select().order_by(OrmAgent.agent_id.asc()))
        states = list(OrmAgentState.select().order_by(OrmAgentState.skill.asc()))
    by_agent: dict[str, list] = {}
    for state in states:
        by_agent.setdefault(state.agent_id, []).append(state)
    items = []
    for agent in agent_rows:
        skills = [{"skill": state.skill, "hash": state.hash,
                   "version": state.version, "updated_at": state.updated_at}
                  for state in by_agent.get(agent.agent_id, [])]
        try:
            capabilities = json.loads(agent.capabilities or "[]")
        except ValueError:
            capabilities = []
        items.append({
            "agent_id": agent.agent_id, "harness": agent.harness,
            "capabilities": capabilities, "first_seen": agent.first_seen,
            "last_seen": agent.last_seen, "skill_count": len(skills),
            "skills": skills})
    return {"items": items}


def register(app, server) -> None:
    conn = server.memo_conn

    @app.get("/api/agents", response_model=AgentList)
    async def agents():
        return await run_in_threadpool(build_agents, conn)

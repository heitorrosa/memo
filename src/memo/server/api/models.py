from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from memo.memory.common import STABILITY


class MemoryLink(BaseModel):
    to: str
    rel: str


class MemoryAlias(BaseModel):
    segment: str
    canonical: str


class MemorySaveBody(BaseModel):
    key: str = Field(min_length=1)
    value: str
    type: str
    namespace: str = "default"
    links: list[MemoryLink] | None = None
    aliases: list[MemoryAlias] | None = None
    agent_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        if value not in STABILITY:
            raise ValueError(f"type must be one of {sorted(STABILITY)}")
        return value


class SkillPutBody(BaseModel):
    name: str = Field(min_length=1)
    description: str
    body: str
    source: str = "local"


class SkillRevisionBody(BaseModel):
    patch: str = Field(min_length=1)


class Counts(BaseModel):
    memories: int
    current: int
    superseded: int
    archived: int
    skills: int
    agents: int
    edges: int
    aliases: int
    episodes: int


class Stats(BaseModel):
    version: int
    vec: str
    ui: bool
    db_path: str
    counts: Counts
    by_type: dict[str, int]
    by_status: dict[str, int]
    by_source: dict[str, int]
    namespaces: list[str]
    last_activity: str | None


class Health(BaseModel):
    ok: bool
    version: int
    vec: str


class Memory(BaseModel):
    key: str
    namespace: str
    value: str
    type: str
    stability: float
    score: float
    uses: int
    status: str
    agent_id: str | None
    family_id: str | None
    relation_type: str | None
    superseded_by: str | None
    created_at: str
    updated_at: str
    valid_from: str | None
    valid_to: str | None
    valid_until: str | None
    degree: int


class MemoryList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[Memory]


class MemoryEdge(BaseModel):
    rel: str
    src_key: str
    dst_key: str
    weight: float
    declarers: list[str]
    status: str
    direction: str


class Alias(BaseModel):
    segment: str
    canonical: str
    status: str
    evidence: int | str | None
    declarers: list[str]


class FamilyRow(BaseModel):
    key: str
    status: str
    valid_from: str | None
    valid_to: str | None
    superseded_by: str | None
    relation_type: str | None
    relation_to_subject: str | None


class Declarer(BaseModel):
    src_key: str
    dst_key: str
    rel: str
    declarers: list[str]
    status: str
    evidence: int | str | None


class RelatedMemory(BaseModel):
    key: str
    type: str
    status: str
    uses: int
    score: float


class MemoryDetail(BaseModel):
    memory: Memory
    edges: list[MemoryEdge]
    aliases: list[Alias]
    family: list[FamilyRow]
    declarers: list[Declarer]
    related: list[RelatedMemory]


class SkillSummary(BaseModel):
    name: str
    description: str
    version: int
    source: str
    hash: str
    created_at: str
    updated_at: str
    body_bytes: int
    revisions: int


class SkillList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[SkillSummary]


class Skill(BaseModel):
    name: str
    description: str
    body: str
    hash: str
    version: int
    source: str
    created_at: str
    updated_at: str


class SkillDetail(BaseModel):
    skill: Skill


class AgentSkillRef(BaseModel):
    skill: str
    hash: str
    version: int
    updated_at: str


class Agent(BaseModel):
    agent_id: str
    harness: str
    capabilities: list[str]
    first_seen: str
    last_seen: str
    skill_count: int
    skills: list[AgentSkillRef]


class AgentList(BaseModel):
    items: list[Agent]


class GraphNode(BaseModel):
    key: str
    type: str
    status: str
    uses: int
    score: float
    degree: int


class GraphEdge(BaseModel):
    src_key: str
    dst_key: str
    rel: str
    weight: float


class GraphLink(BaseModel):
    src_key: str
    dst_key: str
    score: float


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    similar: list[GraphLink]

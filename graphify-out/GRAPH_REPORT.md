# Graph Report - ai-team-orchestrator  (2026-09-11)

## Corpus Check
- 29 files · ~6,841 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 189 nodes · 463 edges · 13 communities (8 shown, 2 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 39 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c6089626`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- AbstractLLMProvider
- NvidiaNimProvider
- .evaluate
- _extract_and_parse_json
- PlannerAgent
- api.py
- models.py
- FastAPI (0.141.1)
- annotated-doc (0.0.5)
- anyio (4.15.1)

## God Nodes (most connected - your core abstractions)
1. `NvidiaNimProvider` - 32 edges
2. `ProviderConfig` - 30 edges
3. `Task` - 27 edges
4. `WorkerAgent` - 26 edges
5. `_extract_and_parse_json()` - 26 edges
6. `PlannerAgent` - 23 edges
7. `ReviewerAgent` - 20 edges
8. `AI_TEAM_ORCHESTRATOR` - 16 edges
9. `AbstractLLMProvider` - 15 edges
10. `AI_TEAM_ORCHESTRATOR` - 13 edges

## Surprising Connections (you probably didn't know these)
- `AI_TEAM_ORCHESTRATOR` --uses--> `WorkerAgent`  [INFERRED]
  main.py → src/agents/worker_agent.py
- `AI_TEAM_ORCHESTRATOR` --uses--> `RunModel`  [INFERRED]
  main.py → src/database.py
- `AI_TEAM_ORCHESTRATOR` --uses--> `TaskModel`  [INFERRED]
  main.py → src/database.py
- `AI_TEAM_ORCHESTRATOR` --uses--> `NvidiaNimProvider`  [INFERRED]
  main.py → src/providers/base_provider.py
- `AI_TEAM_ORCHESTRATOR` --uses--> `ProviderConfig`  [INFERRED]
  main.py → src/providers/base_provider.py

## Import Cycles
- None detected.

## Communities (13 total, 2 thin omitted)

### Community 0 - "AbstractLLMProvider"
Cohesion: 0.13
Nodes (11): AbstractLLMProvider, ProviderError, ProviderResponse, ProviderUsage, Any, BaseModel, Exception, Token usage information returned by a provider. (+3 more)

### Community 1 - "NvidiaNimProvider"
Cohesion: 0.13
Nodes (21): Worker Agent – executes a single task sequentially., Agent that executes a single task and returns its output., Execute the given task and return a WorkerResult., Parse the provider's structured JSON response., WorkerAgent, NvidiaNimProvider, ProviderConfig, Provider abstraction layer for LLM calls. All providers must implement the… (+13 more)

### Community 2 - ".evaluate"
Cohesion: 0.40
Nodes (3): Any, Evaluate the worker output and return a structured ReviewResult., Parse the provider's structured JSON review.

### Community 3 - "_extract_and_parse_json"
Cohesion: 0.11
Nodes (24): Planner Agent – decomposes a user goal into 3–7 sequential tasks., _extract_and_parse_json(), JSONParseError, Any, Exception, Shared JSON parsing utilities for LLM responses., Extract and parse JSON from LLM response text. Handles: - Markdown code fences…, Raised when LLM response cannot be parsed as valid JSON. (+16 more)

### Community 4 - "PlannerAgent"
Cohesion: 0.13
Nodes (22): command, AI_TEAM_ORCHESTRATOR, execute(), Synchronous entry point used by FastAPI backend., Execute the AI team orchestration pipeline via CLI., Main orchestrator for the AI Team MVP., run_orchestrator(), Agents package for the AI Team MVP. (+14 more)

### Community 6 - "api.py"
Cohesion: 0.09
Nodes (26): execute(), ExecutionRequest, get_run(), health_check(), list_runs(), BaseModel, Session, DeclarativeBase (+18 more)

### Community 7 - "models.py"
Cohesion: 0.10
Nodes (26): Enum, Run the planner on the given goal and return a structured plan., Parse and validate the provider's structured JSON plan., Reviewer Agent – validates task output and decides acceptance., Artifact, PlannerOutput, BaseModel, Pydantic models for the AI Team MVP. (+18 more)

### Community 8 - "FastAPI (0.141.1)"
Cohesion: 0.50
Nodes (4): ai-team-orchestrator, FastAPI (0.141.1), pydantic (2.13.5), uvicorn (0.52.4)

## Knowledge Gaps
- **5 isolated node(s):** `anyio (4.15.1)`, `ai-team-orchestrator`, `pydantic (2.13.5)`, `uvicorn (0.52.4)`, `annotated-doc (0.0.5)`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 85 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `_extract_and_parse_json()` connect `_extract_and_parse_json` to `NvidiaNimProvider`, `.evaluate`, `models.py`?**
  _High betweenness centrality (0.190) - this node is a cross-community bridge._
- **Why does `NvidiaNimProvider` connect `NvidiaNimProvider` to `AbstractLLMProvider`, `_extract_and_parse_json`, `PlannerAgent`, `models.py`?**
  _High betweenness centrality (0.078) - this node is a cross-community bridge._
- **Why does `AI_TEAM_ORCHESTRATOR` connect `PlannerAgent` to `AbstractLLMProvider`, `NvidiaNimProvider`, `api.py`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `NvidiaNimProvider` (e.g. with `AI_TEAM_ORCHESTRATOR` and `AI_TEAM_ORCHESTRATOR`) actually correct?**
  _`NvidiaNimProvider` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `ProviderConfig` (e.g. with `AI_TEAM_ORCHESTRATOR` and `AI_TEAM_ORCHESTRATOR`) actually correct?**
  _`ProviderConfig` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `Task` (e.g. with `AI_TEAM_ORCHESTRATOR` and `PlannerAgent`) actually correct?**
  _`Task` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `WorkerAgent` (e.g. with `AI_TEAM_ORCHESTRATOR` and `AbstractLLMProvider`) actually correct?**
  _`WorkerAgent` has 6 INFERRED edges - model-reasoned connections that need verification._
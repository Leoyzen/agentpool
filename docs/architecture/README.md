# AgentPool Architecture Documentation

This branch hosts the global architecture documentation for AgentPool. It is
designed to answer the questions that are hard to reverse-engineer from code or
scattered PRs:

- What problem are we solving?
- Why this solution and not another?
- What are the constraints we accept?
- How do the pieces fit together?
- Which decisions are still open?

## Why this branch exists

AgentPool is a multi-protocol, multi-package agent orchestration framework. Its
feature surface (sessions, teams, ACP, MCP, AG-UI, OpenCode) has grown quickly.
Fast iteration is valuable, but it can also produce designs that are only fully
visible in the lead engineer's head. This documentation branch exists to make
the reasoning explicit, reviewable, and version-controlled.

It is **not** a replacement for RFCs, ADRs, or code comments. It is the layer
above them: the narrative that connects local decisions into a coherent system.

## How to read this documentation

| Document | Purpose | Start here if you want... |
|---|---|---|
| [01-vision-and-philosophy](./01-vision-and-philosophy.md) | Design philosophy, scope, and non-goals | ...to understand what AgentPool is and is not |
| [02-system-overview](./02-system-overview.md) | Global architecture diagram and component map | ...to see how the pieces fit together |
| [03-problem-space](./03-problem-space.md) | Problems we are solving and evidence | ...to understand why we are building this |
| [04-constraints-and-principles](./04-constraints-and-principles.md) | Hard constraints and design principles | ...to know what we cannot compromise |
| [05-framework-comparison](./05-framework-comparison.md) | Comparative survey of multi-agent frameworks | ...to see how our choices compare to the field |
| [06-rfc-roadmap](./06-rfc-roadmap.md) | Existing RFCs, dependencies, and phase plan | ...to navigate the RFC landscape |
| [07-team-mode-design-space](./07-team-mode-design-space.md) | Design space for Dynamic Agent Team | ...to understand the dimensions and choices of team design |
| [08-agent-abstraction-unification](./08-agent-abstraction-unification.md) | Unified abstraction of Agent, Subagent, and Team Member | ...to see how agent types collapse into one class with lifecycle policies |
| [06-decisions/](./06-decisions/) | Design decision records (DDRs) | ...to review specific architectural decisions |
| [Team Mode: RFC-0055 design notes](../team-mode/RFC-0055-design-notes.md) | Local implementation notes for Dynamic Team Mode | ...to connect RFC-0055 to the current codebase |

## Status

This documentation is a living draft. It is intentionally written in a separate
docs branch so that architecture discussions can proceed in parallel with code
development. When a section stabilizes, it should be referenced from the relevant
RFC or merged into `docs/` on the main development branch.

## Contributing

When you propose a new architectural direction, add or update the relevant
section here before opening the implementation PR. The minimum bar is:

1. State the problem before the solution.
2. List explicit constraints.
3. Describe at least one alternative you considered and rejected.
4. Record the decision in `06-decisions/` if it is not already covered by an RFC.

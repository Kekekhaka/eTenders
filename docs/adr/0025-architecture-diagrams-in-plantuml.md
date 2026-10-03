# ADR-0025: Architecture diagrams are drawn in PlantUML (C4-PlantUML), not Mermaid

- **Status:** Accepted (operator instruction, 2026-09-30)
- **Date:** 2026-09-30
- **Requirements:** NFR-09 (readable by one person); docs-as-code
- **Source:** operator instruction of 2026-09-30; DESIGN_SPEC v0.2 header, §3, §5.1, §7, §17

## Context
DESIGN_SPEC v0.1 drew its diagrams in Mermaid. On 2026-09-30 the operator instructed that the
architecture diagrams use PlantUML, with the C4 model notation.

## Decision
- **Architecture diagrams are drawn in PlantUML, using the C4-PlantUML standard library**
  (`!include <C4/C4_Context>`, `<C4/C4_Container>`, `<C4/C4_Component>`). Sequence and state
  diagrams use plain PlantUML.
- **The sources live in `docs/diagrams/`**, one `.puml` file per diagram:
  - `c4-context.puml`;
  - `c4-container.puml`;
  - `c4-component.puml`;
  - `run-sequence.puml`;
  - `memory-day-states.puml`;
  - `trust-boundaries.puml`.

  The same text is embedded in DESIGN_SPEC as `plantuml` code blocks. The `.puml` file is the source.
- **Scope:** architecture diagrams. The REQUIREMENTS_PACK process flow (§6.1) and operator journey
  (§6.2) stay in Mermaid until the operator decides about them.

## Consequences
- **GitHub doesn't render PlantUML in Markdown.** In the repo, the `plantuml` blocks and the
  `.puml` files show as source text, not pictures. To view a diagram, render it with a
  PlantUML toolchain (Java plus PlantUML, or an IDE plugin) or check in exported images.
- **Not rendered or syntax-checked yet.** Java and PlantUML aren't installed on the operator's
  machine (`java -version` and `plantuml -version` report "command not found", DESIGN_SPEC header).
  Someone needs to render them once to confirm they're correct.
- A public PlantUML server would send the diagram text off the machine, and the corporate proxy may
  block it. Prefer a local render. The diagrams hold no secrets, but they do describe the security
  design (trust boundaries).
- The diagrams now use two notations (PlantUML for architecture, Mermaid in the REQUIREMENTS_PACK)
  until the operator decides about the Mermaid ones.

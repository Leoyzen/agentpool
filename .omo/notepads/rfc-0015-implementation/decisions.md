# RFC-0015 Implementation Decisions

## Decision: Single-Property Object Schema
**Applied**: Object schemas with only 1 property will use existing single-question flow
- Multi-question handler only triggers for `len(props) >= 2`
- Less disruption to existing behavior
- Matches current RFC specification

## Decision: Unsupported Property Types
**Applied**: Option C - Convert unsupported types to text (fallback to string behavior)
- Most flexible approach - users can always provide an answer
- Implementation: fallback to `{"type": "string"}` behavior with empty options

## Answer Key Format
- MUST preserve original property keys from schema
- Must NOT convert to q{i} format (per Metis gap analysis)

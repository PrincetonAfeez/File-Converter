# 1. Record architecture decisions

Date: 2026-07-09
Status: Accepted

## Context

The system spec (`File Converter.md`) references ADR identifiers, but the decisions were not
captured as durable records in the repository.

## Decision

We record significant architectural decisions as Markdown ADRs in `docs/adr/`, numbered
sequentially, using the Nygard format (Context / Decision / Consequences).

## Consequences

- Reviewers and new contributors can see why the system is shaped the way it is.
- Superseded decisions are kept (marked `Superseded by NNNN`) rather than deleted.

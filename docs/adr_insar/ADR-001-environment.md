ADR-001-environment.md

# Study environment

## Context
We've different options for the python project environment.

## Problem
Select best environment

## Alternatives
| Option | Reproducibility | Native dependencies | Simplicity | Fit here |
|---|---|---|---|---|
| `venv + pip` | Moderate | Weak | Excellent | Acceptable initially |
| Conda | Good | Excellent | Moderate | Good |
| Poetry | Good | Weak | Good | Better for pure Python |
| `uv` | Excellent | Weak | Excellent | Excellent for pure Python |
| **Pixi** | **Excellent** | **Excellent** | **Good** | **Very good** |

## Decision
Pixi is select as environment.

## Advantages
Pixi is a good option because works well with GDAL and scientific binaries.

## Disadvantages

## Consequences
Environment/dependency manager: Pixi

Initial environment:
    Python
    + development tooling
    + only Phase-1 dependencies

Later:
    add scientific dependencies as they become necessary

Eventually:
    Docker provides the execution/container boundary
    Pixi provides reproducible dependency resolution
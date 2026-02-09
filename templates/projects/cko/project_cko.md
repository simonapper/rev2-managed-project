{# templates/projects/cko/project_cko.md #}
{# 7-bit ASCII only. #}
# ============================================================
# PROJECT CKO - CANONICAL DEFINITION
# This file governs the creation, approval, dispute, and retirement of organisational anchor points.
# ============================================================
# CKO ID: CKO-PROJECT-{{ project.id|stringformat:"06d" }}
# Project Name: {{ project.name|default:"" }}
# Owner: {{ owner_username|default:"" }}
# Date: {{ today }}
# Status: DRAFT

## Canonical summary (<=10 words)
- {{ f.canonical_summary|default:"(not set)" }}

## Identity (what)
- Project type: {{ f.identity_project_type|default:"(not set)" }}
- Project status: {{ f.identity_project_status|default:"(not set)" }}

## Intent (why)
### Primary goal
{{ f.intent_primary_goal|default:"(not set)" }}

### Success / acceptance criteria
{{ f.intent_success_criteria|default:"(not set)" }}

## Scope (boundaries)
### In-scope
{{ f.scope_in_scope|default:"(not set)" }}

### Out-of-scope
{{ f.scope_out_of_scope|default:"(not set)" }}

### Hard constraints
{{ f.scope_hard_constraints|default:"(not set)" }}

## Authority model (truth resolution)
### Primary authorities
{{ f.authority_primary|default:"(not set)" }}

### Secondary authorities
{{ f.authority_secondary|default:"(not set)" }}

### Deviation rules
{{ f.authority_deviation_rules|default:"(not set)" }}

## Interpretive / operating posture (how)
### Assumptions and uncertainties
{{ f.posture_epistemic_constraints|default:"(not set)" }}

### Innovation rules
{{ f.posture_novelty_rules|default:"(not set)" }}

## Storage and durability (where truth lives)
- Artefact root: {{ f.storage_artefact_root_ref|default:"(not set)" }}

## Canonical context (reference narrative)
{{ f.context_narrative|default:"(not set)" }}

## Stability declaration
- Internally consistent
- Governed by the stated authority model
- Safe to use as canonical project truth

Any deviation must be deliberate, versioned, and documented.

# ============================================================
# END PROJECT CKO
# ============================================================

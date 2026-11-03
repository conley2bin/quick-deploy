# Responsibility registry

Format: responsibility-registry/v3
Era: template
Revision: 0
Audit-Head: none

Edit the visible rows. Details are additional fields, never duplicate columns.
To start over: delete this live file, then copy RESPONSIBILITIES.template.md here.
Reset changes registered authority; it does not stop running agents.
Parent values: user or owner:ID (bare stable ID). Use the CLI to appoint after a template reset.
Manual deletion loses current rows. Private audit does not restore current authority; prefer agent-managed clear for a complete backup.

## Owners
| ID | Name | Role | Parent | Details (JSON) |
| --- | --- | --- | --- | --- |

## Assignments
| ID | Owner | Status | Project ID | Task | Objective | Acceptance | Details (JSON) |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Coordination
| ID | Status | Integration owner | Details (JSON) |
| --- | --- | --- | --- |

## History (audit only; never restores current rows)
Audit history is stored separately in the private responsibility-registry store.

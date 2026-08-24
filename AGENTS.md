# Agent guidance

## Guiding principle

**Code is a liability; less is more.** Prefer the smallest coherent
solution that satisfies the task and preserves the project's invariants.
Remove obsolete complexity when it is in scope and
verified. Do not turn a local task into an unrelated rewrite.

Documentation is maintained surface area too. Add information only when it
helps a named reader decide, act, verify, or recover.

## Repository map

- Product scope and shortest user path: `README.md`
- Task-oriented documentation map: `docs/index.md`
- Development rules and verification: `CONTRIBUTING.md`
- Documentation policy: `docs/documentation-guidelines.md`
- Agent documentation workflow: `docs/documentation-agent-checklist.md`
- Current validation claims: `docs/validation-status.md`
- Dated validation evidence: `docs/validation-history.md`

## Documentation review gate

Apply `docs/documentation-agent-checklist.md` for the changes named in its
"When to use this checklist" section.

Compare the change with the relevant canonical documentation and
`docs/index.md`.

When changes are authorized, update the existing canonical documentation.
During review-only tasks, report missing, stale, conflicting, or misplaced
documentation instead of editing it.

Do not require optional pages, generic tutorials, speculative failure cases, or
documentation added solely for completeness. Do not present planned behavior as
available or make validation claims without scoped evidence.

## Verification

For documentation-only changes, run `python3 tests/check-project.py`. On hosts
where the executable is named `python`, use `python tests/check-project.py`.

For executable code, configuration, Compose, or runtime changes, follow the
verification requirements in `CONTRIBUTING.md`.

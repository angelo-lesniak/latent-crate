# Agent guidance

Be extremely concise. Sacrifice grammar for the sake of concision.

## Guiding principle

Less is more. Prefer the smallest coherent solution that satisfies the
task and preserves the project's invariants. Remove obsolete complexity
when it is in scope and verified. Do not turn a local task into an
unrelated rewrite. Code that no longer earns its place is removed, not
patched around.

Keep documentation grounded in code, and never explain what a module
does when reading it shows that. The main sources here are the usage
text for syntax, `.env.example` for settings and defaults, `pins.env`
for pins, and the manifests for set contents. A page links to the source
instead of restating it. A page holds the glue no single file shows: what
a user needs first, and where the deep dive is.

## Know this repository

[Layout](CONTRIBUTING.md#layout) says where CLI code belongs;
[Tests](CONTRIBUTING.md#tests) says what the static gate runs and which
GPU checks a change needs; [Documentation
changes](CONTRIBUTING.md#documentation-changes) says which page owns
which facts and what the link check covers. Check every other fact by
hand against the implementation.

## Code

- Read the module you change before writing, and follow its names, error
  style, and structure, even where you would have chosen otherwise. New
  terms come from the page that owns the topic.
- Never swallow exceptions. Logging without rethrowing still counts as
  swallowing.
- Do not increase a timeout as a first response to a failure. Find the
  cause.
- No abstraction for one caller, no option nobody asked for, no
  compatibility shim.
- A comment says why, or names the evidence behind a choice that is not
  obvious; it never restates the code.
- A change describes the new behavior only: no comment, docstring, or
  test says what the code did before. A choice a reviewer might overturn
  gets its reason once, in one sentence, where the choice is made.
- Delete what a change makes unused, tests and documentation included.
- A behavior change arrives with its test in the gate and its line on
  the owner page, in the same change.

## Documentation review gate

Before you write, restructure, or review documentation, or change anything
user-visible, load the `documentation-guidelines` skill from
[SkillCrate](https://github.com/angelo-lesniak/skill-crate);
[Documentation changes](CONTRIBUTING.md#documentation-changes) says how
to obtain it and is the checklist it asks for.

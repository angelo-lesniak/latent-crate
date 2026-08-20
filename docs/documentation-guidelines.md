# Documentation guidelines

Use these guidelines to plan, write, and review documentation for public or
private software projects. They apply to products, services, libraries, tools,
and infrastructure repositories.

This guide helps you decide what information a project needs, where that
information belongs, and how much detail to include.

For a compact workflow that a coding or documentation agent can follow, use
[the companion agent checklist](documentation-agent-checklist.md).

## How to apply this guide

This guide is not a template. Do not create every section or page mentioned
here, and do not treat every listed item as required. Numeric limits, suggested
structures, and review questions are prompts to inspect the text, not metrics
to satisfy. Do not count words, headings, or page types to claim compliance.

If two goals conflict, use this order:

1. Keep the information correct and the reader safe.
2. Help the reader complete the task and recover from realistic problems.
3. Keep each fact consistent with its main source.
4. Make the text concise and follow the preferred style.

## Decide what information is required

Use these three levels:

- **Core:** Include information that a specific reader group needs for its
  main task. Make it easy to find. It can appear in a README, command help,
  generated API page, source header, or team runbook. It does not need a
  separate page.
- **Required when relevant:** Include the information only when the condition
  described in this guide applies to the project. Include it on an existing
  page, generate it, or provide a link to its **main source**: the place where
  the fact is maintained and kept current.
- **Optional:** Add the information only when feedback, testing, support
  questions, or another clear observation shows that it helps readers.

These levels decide whether information is needed. They do not decide whether
the information needs a separate page.

## Name the reader

Before writing, identify who needs the information. A **user** uses the
product, service, tool, or API. A **developer** changes the implementation. A
**maintainer** is responsible for technical direction, releases, and long-term
operation. An **operator** deploys and monitors a running system or restores it
after a failure. A **contributor** is an external or occasional developer in a
project that accepts contributions.

These roles can overlap, and a user can also be a developer. Use the terms that
fit the project. A private project usually needs developer and maintainer
information, not instructions for external contributors. Different reader
groups often need different starting points for their main tasks.

## Follow six rules for every documentation task

Adjust the amount of detail to the size and risk of the project:

1. State who the documentation is for and what that reader needs to do.
2. Show the shortest correct way to get a useful result.
3. Explain unexpected costs, risks, and limits before the affected action or
   decision.
4. Explain how to recognize success when the result is not clear.
5. Maintain each changing fact in one main source. If a fact comes from
   several sources, define how it is produced.
6. Stop when the reader has enough information to decide and act safely.

**Obvious** means the intended reader should already know something and this
project does not behave differently. It is not obvious merely because the
author knows it.

## Decide how much detail to include

Consider these three factors separately:

| Factor | Ask | Effect on the documentation |
| --- | --- | --- |
| Reader need | How many readers need this, and how often? | How easy the information is to find and how early it appears |
| Harm from mistakes | What happens if the reader gets it wrong? | Warning strength, success checks, recovery help, and how carefully to review it |
| Rate of change | How often can the fact or process change? | Who maintains the source, whether to generate it, how to keep copies current, and how often to review it |

A rare recovery action that can delete data needs strong warnings in its
focused runbook, not a prominent place on every reader's starting page. A
common low-risk command needs prominent placement but little safety explanation.

### Decide whether information belongs

Before adding or retaining information, ask:

1. Does it change the intended reader's decision or action?
2. Could omission cause a realistic failure or misunderstanding?
3. Is it specific to the project or its use of an external technology?
4. Is this the right place to maintain the fact or explain what it means?
5. Is the intended reader group likely to need it here?

If most answers are no, remove the information or replace it with a link.

A failure is **plausible** only when behavior, tests, recorded problems, or
normal dependency behavior show that it can happen. Do not invent readers,
failures, differences, alternatives, or support claims for completeness.

### Remove information that no longer helps

Delete, merge, redirect, or archive documentation when:

- the feature, workflow, or audience no longer exists;
- an interface, schema, command, or reliable external source now answers the
  question well enough;
- the condition that required the information no longer applies;
- two pages serve the same purpose;
- an example or screenshot is stale and no longer worth maintaining.

Follow the project's retention policies for legal, security, incident, release,
and validation records. Keep historical records outside current task navigation
unless readers need them. Do not delete a retained record merely because the
current documentation does not link to it.

### Add information only when its condition applies

When a condition in this table applies, provide the information. Create a
separate page only for a distinct reader or task, or when the information
would make an existing page's main task hard to find.

| Information | Include it when... |
| --- | --- |
| Intended readers, suitability, or differences | Readers must decide whether the project meets their needs or how they can use it |
| Task-based navigation | Readers can no longer scan the available routes from the starting point |
| Glossary | Readers repeatedly encounter project-specific or easily confused terms |
| Terminology register | Several writers need to use repeated terms consistently |
| Configuration reference | Readers can meaningfully change several settings |
| API or compatibility policy | Other software depends on stable behavior, versions, or deprecation rules |
| Version or variant scope | Readers can encounter multiple maintained releases, API versions, deployment modes, or significantly different configurations |
| Support or validation status | The project makes compatibility, reliability, or maintenance claims |
| Troubleshooting | Failures recur, are non-obvious, or are expensive to diagnose |
| Security information | Project-specific choices affect credentials, private data, untrusted input, or network exposure |
| Operations information | The project requires substantial deployment, monitoring, failure handling, backup, or recovery work |
| Upgrade information | Updates require migrations, compatibility decisions, or manual steps |
| Architecture information | Developers need boundaries or decisions to make safe changes |
| Developer setup | Another person, or the author after a long gap, could not reproduce the environment safely |
| Contribution process | External or occasional contributors need a workflow different from normal development |
| Ownership or help | Existing team systems do not clearly show who is responsible or how to get help |
| Changelog | Users or dependent teams need to understand behavior changes |
| FAQ | Questions have actually recurred |
| Roadmap | A specific reader group needs to know the project's future direction |

Link to an adequate organization-wide policy instead of copying it. Generate
API signatures, command references, versions, and defaults when code, schemas,
configuration, or structured metadata maintain them. Hand-written text should
explain decisions, meaning, and project-specific consequences.

When several versions or variants coexist, each starting point or page must say
which ones it describes or provide a clear selector. Archived or older material
must not appear to describe the current version.

## Give each page one main purpose

The title, introduction, headings, and examples should mainly serve one task or
question. Move a secondary subject when it is useful on its own or hides the
main task. Do not split a short page that is already clear.

Common page types answer different questions:

| Page type | Main question |
| --- | --- |
| Entry page | What is this, what can I do first, and what important limit affects use? |
| Tutorial | How can I succeed for the first time? |
| How-to guide | How do I complete this task? |
| Reference | What are the exact commands, fields, defaults, constraints, or APIs? |
| Explanation | Why does this concept or design work this way? |
| Troubleshooting | What caused this symptom, and how do I recover? |
| Developer guide | How do I set up, test, change, and review this project safely? |
| Operations guide | How do I operate and diagnose the system, limit an incident, and restore service? |
| Evidence page | What was checked, under which conditions, and when? An example is a validation-status table |

### Make the entry page earn the reader's attention

On the first screen of an entry page, answer three questions in this order:
what does the project do, what makes it different from the obvious
alternative, and what is the shortest tested path to a first useful result?
State the time, download size, and disk cost of that path before its first
command. Put project status and scope limits after the first path unless a
risk is attached to an immediate action.

Readers of a public project always face an adoption decision. State what
distinguishes the project as a concrete consequence the reader can verify, not
as an adjective. A factual contrast with a category of alternatives is
allowed, for example "a desktop installer is simpler if you only need X."
Invented benchmarks and unverifiable comparison claims are not. For a private
project, explain intended readers, alternatives, and differences only when
readers face a real selection decision.

Keep two layers: a short path that takes a prepared reader to a first result,
and deep-dive pages for decisions, options, and failures. The short path links
to deep dives instead of explaining. A deep-dive page does not restate the
short path's commands. End the short path with one link for each likely next
need: the task failed, different hardware, or the next task.

### Include only the procedure details readers need

Every procedure needs a goal and an action. Also include:

- prerequisites or consequences when they are non-obvious;
- an expected result when success is not clear;
- a success check when the action can appear to succeed but fail;
- recovery instructions when failure is plausible or costly;
- a next step when readers commonly need one.

Keep one meaningful action or decision in each step. Keep commands together
when separating them would add noise or hide how they depend on each other.

Make each example the smallest complete case a reader can run without edits or
with clearly marked placeholders. Use one example per task; add a second only
when readers face a real branching decision. When an automated check cannot
run an example, state the version or date it was written for.

For emergency procedures, start with safe assessment, containment, and clear
conditions for stopping. Put each risk next to the action that creates it. Do
not delay a safe diagnostic step with unrelated background information.

A troubleshooting entry normally contains a symptom, likely cause, diagnostic
step, fix, and success check. Add instructions for asking for help only where
a support channel actually exists.

## Separate promises from evidence

Status words answer different questions. One feature can be supported,
experimental, deprecated, and validated at the same time. Define only the terms
the project uses.

| Question | Example terms | What the terms mean |
| --- | --- | --- |
| Will the team maintain it? | Supported / unsupported | The team's intent to maintain the stated feature, version, or environment; not proof that it works |
| How strong is the compatibility promise? | Experimental / stable | The level of compatibility and reliability the team promises |
| Is it still intended for use? | Active / deprecated / retired | Whether new use is encouraged and how long it will remain available |
| What evidence exists? | Validated / tested / expected | Whether a defined procedure passed, a limited check ran, or technical evidence supports the claim without recorded validation |

A validation claim must name the tested version or build, environment,
procedure, date, and important limits. If the result can become outdated, say
when to repeat the test or stop using the result. Historical evidence must not
appear current.

Describe expected results declaratively: "If startup succeeds, the status is
`healthy`." Do not use "should" to hide uncertainty about actual behavior.

## Write technical English for an international audience

Some rules below are based on ASD-STE100 Simplified Technical English, but
this guide does not use its full ruleset or dictionary, does not claim
compliance, and automated tools cannot certify compliance.

Plain language can still have a confident and engaging voice. Prefer concrete
benefits and vivid examples over marketing adjectives:

| Avoid | Prefer |
| --- | --- |
| A blazingly fast, next-generation experience | A workflow that works today still works after a rebuild, because every component is pinned |

### Vocabulary and terminology

- Use one preferred term with one intended meaning for each concept.
- Preserve established technical names and exact commands, paths, configuration
  keys, API names, user-interface labels, error messages, and quotations.
- Define necessary domain terms. Clarify words that can have several meanings.
- Avoid contractions outside exact quotations or user-interface text.
- Avoid slang, jokes, metaphors, and culture-specific references.
- Prefer one literal verb. For example, use "start" instead of "spin up."
- Write a specific noun after "this" when the reference could be unclear, such
  as "this command" or "this file."
- Review a phrase that combines more than three nouns, adjectives, or technical
  terms. Rewrite it when the relationship between the words is unclear.

Examples:

| Avoid | Prefer |
| --- | --- |
| Spin up the service | Start the service |
| Dirty checkout | Working tree with uncommitted changes |
| The test blew up | The test failed |

### Sentences and instructions

- Use one main statement per sentence and one topic per paragraph when this
  makes the text easier to understand.
- Prefer active voice. Use passive voice when the actor is unknown or irrelevant.
- Start instructions with a direct command, such as "Run," "Open," "Set," or
  "Check."
- Put a condition before the action it controls.
- Repeat the noun when a pronoun could refer to more than one thing.
- Review an instruction that has more than 20 words and a descriptive sentence
  that has more than 25 words.
- Do not remove necessary subjects, objects, or context merely to shorten text.

Use these words consistently:

- **must** for a requirement and **must not** for a forbidden action;
- **should** for recommendations;
- **can** for an ability or function;
- **might** for an uncertain event or result;
- **optional** when the procedure remains valid without the item.

Use exact values when they are known and unlikely to change. Otherwise, label an
estimate or range and include the assumptions, source, or date needed to
interpret it.

Expand an acronym when the intended reader might not know it and the expansion
helps the task. A nearby glossary or shared terminology list can avoid repeated
expansions across the documentation.

Use unambiguous dates, include units, state time zones where relevant, and do
not rely on color or screenshots alone to communicate instructions.

### Glossary and terminology register

A glossary helps readers understand repeated, project-specific, or easily
confused terms. Keep each definition short. Do not turn the glossary into a
general technical encyclopedia.

When several writers must use terms consistently, a terminology register can
map each concept to a preferred term, a definition, an allowed short form, and
terms to avoid. An exact command, path, API name, interface label, error, or
quotation can still use an avoided term.

## Keep documentation correct

For a normal release, update the documentation before readers can use the new
behavior. Link code and documentation changes to the same delivery or release.
After an emergency change, track the documentation task with a responsible
person and a due date or completion condition.

A changing fact can use code, configuration, a schema, structured metadata, a
generated view, a dashboard, or hand-written text as its main source. A defined
and tested method can also produce a fact from several sources, for example a
support table generated from the CI test matrix.

Generate exact facts or link to their main source when practical. If safety or
offline work requires a copy, name the main source. Automate synchronization or
assign someone to keep the copy current.

Automate a check when it reliably finds a real problem:

- broken local links and heading anchors;
- pages that are missing from navigation for their intended readers;
- documented commands or flags that no longer exist;
- examples that can run safely in a test environment;
- inconsistent preferred terminology.

Check external links separately or on a schedule so temporary failures do not
block normal changes.

Treat style automation as review assistance, not as the final decision. Correct
facts and clear meaning are more important than automatic style rules.

## Review only what matters

These questions help reviewers find problems. Choose those that match the
reader, condition, and page purpose.

- Is it clear who the intended reader is and what that reader needs to do?
- Is the shortest correct way to start or complete the task easy to find?
- Does the page have one main purpose?
- Does each unexpected consequence appear next to the affected action?
- Does the text state success, evidence, and limits precisely?
- Does every changing fact have an appropriate main source?
- Can general, obvious, speculative, or repeated information be removed?
- Is information marked "required when relevant" present only because its
  condition applies?
- Does the text use terms consistently without sounding mechanical?
- Can a person from the intended reader group complete the task without
  knowledge that only the author has? A realistic scenario test can also answer
  this question.

Stop when the reader has enough information for the required decision or task.
Do not add information only to make the documentation appear complete.

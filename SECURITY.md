# Security policy

LatentCrate is intended for a trusted single-user or trusted-team environment.
It is not a hardened multi-user service.

ComfyUI Manager and custom nodes install and execute third-party code. The UI
therefore binds to localhost by default. Do not expose it to an untrusted network
without authentication and network controls maintained outside this project.

Use the repository's **Security → Report a vulnerability** action to open a
private GitHub security advisory. If that action is temporarily unavailable,
open a public issue containing no vulnerability details and ask a maintainer to
establish a private channel.

Never place credentials in shared `.env` files, frontend Git URLs, Python
requirement files, image build arguments, logs, exported ComfyUI workflows, or
GPU reports.

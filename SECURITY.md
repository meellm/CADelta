# Security Policy

## Supported versions

Only the latest release on the `main` branch receives security fixes.
Older versions are unsupported.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

Email `info.meellm@gmail.com` with:
- A short description of the issue.
- Steps to reproduce (a minimal STEP file, settings snippet, or command
  line that triggers it).
- The CADelta version (the release tag, or the `.exe` filename you
  downloaded) and the OS you're running on.

You'll get an acknowledgement within 72 hours. Once a fix lands we'll
coordinate disclosure timing with you and credit you in the release
notes (unless you'd rather stay anonymous).

## Scope

In scope:
- Code execution triggered by reading a crafted STEP file.
- Path-traversal or arbitrary-write bugs when writing the output STEP,
  GLB, JSON, or Excel report.
- Leaks of local file contents or paths into the generated output.
- Unexpected network requests (CADelta runs fully offline; it should
  never reach out to a remote host).

Out of scope:
- Crashes or undefined results on malformed or non-conformant CAD input
  that don't lead to code execution or arbitrary writes; please file
  those as ordinary bug reports.
- Bugs in dependencies (OCCT / `cadquery-ocp`, PySide6, etc.); please
  report those upstream; we'll pick up patched releases on the next
  dependency bump.
- Social engineering or physical-access scenarios.

# CalorieK repository instructions

This repository is the CalorieK project.

Linear:
- Workspace: Atticum
- Team: Atticumlos
- Issue prefix: CAL
- Project: CalorieK
- Current milestone: v0.0.3

The Linear fields above are also read by the handoff packer; update them here
when project metadata changes. The development milestone is not an application
version or a claim that a release has been published.

When a task references a Linear issue such as CAL-2:

1. Read the complete Linear issue through the Linear MCP first.
2. Treat the issue description, acceptance criteria, dependencies, comments, and execution constraints as authoritative.
3. Modify only this repository unless explicitly instructed otherwise.
4. Do not run `git commit`, `git push`, create tags, or publish GitHub Releases unless explicitly instructed.
5. Codex may create or edit tests, but must NOT execute tests by default.
6. Do not run targeted unit tests, the full test suite, GUI smoke tests, PyInstaller, packaging, release validation, or other test/validation commands unless the user explicitly requests that exact command or validation.
7. Necessary non-test static checks such as syntax inspection, import inspection, or source-level consistency checks are allowed when useful.
8. After code changes, stop and let the user run `package_caloriek_handoff.bat`.
9. ChatGPT reviews the handoff ZIP and generates a task-specific Windows BAT.
10. The user runs that BAT locally. Its results are the authoritative validation evidence for the issue.
11. Only after ChatGPT review and Windows validation pass should an implementation issue be marked Done.
12. The dedicated release-validation issue for the target version/milestone owns the release gate. Release packaging, tag creation, push, and GitHub Release publication require explicit authorization and are not normal implementation tasks.

Release process:
- Implementation changes remain uncommitted until review.
- Test execution belongs to the ChatGPT-generated Windows BAT workflow.
- Use the dedicated release-validation issue for the target version/milestone, not a historical issue as a permanent gate.
- Independent implementation review and Windows validation precede release packaging/publication. Source changes alone do not constitute a release.
- A release is complete only after the packaged Windows ZIP is validated and the actual GitHub Release asset is re-downloaded and smoke-tested.

Review handoff (not a release build):
- The user runs `package_caloriek_handoff.bat`; Codex must not run it unless explicitly authorized.
- Default: source/docs/tests/scripts and Git/environment metadata, without historical test results or runtime data.
- `withresults` adds local validation-result directories; `withdata` adds repository-local data only after an explicit privacy confirmation. Both options may be combined in either order.
- Metadata may reveal local paths and Git remote information. Inspect every handoff before sharing; never publish it as a Windows release asset.

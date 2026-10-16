<!-- satellite-contributing -->
# Contributing

Pull requests are welcome here. Send them to this repository, not to the
Factory repository: history on this repository is append-only (no force-push),
and a merged pull request is imported back into the canonical AI-Factory
monorepo and synced here again, so your commit stays in the history.

1. Open an issue first for anything larger than a fix, so the change is agreed
   before you write it.
2. Fork, branch from `main`, keep the change focused, and add a test for it.
3. Run the checks from this repository's README before you open the pull request.

If the notice at the top of the README calls this repository a read-only mirror,
open an issue with a minimal reproduction, the affected version, expected and
actual results, and a proposed patch instead: a mirror sync replaces its commits.

Use this repository's README for installation and tests. The Factory's
web/frontend and deployment scripts are not prerequisites for this package.
Do not send satellite changes to the Factory repository.

Never include keys, tokens or personal data in reports. Follow SECURITY.md for
security issues instead of reporting them publicly.

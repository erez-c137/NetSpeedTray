# Release notes

`release.yml` publishes the GitHub release body from **`release-notes/<tag>.md`** - for example
`release-notes/v2.2.0.md` or `release-notes/v2.2.0-rc.1.md` - when that file exists in the tagged
commit.

Why: the release is public the moment CI creates it, and a final one is immediately *Latest* - the
release every installed copy's update dialog reads. Notes applied by hand afterwards left a window
in which users were offered an update with an empty description.

How to use it:

1. Write the notes (brief, user-facing - the changelog carries the technical detail).
2. Commit them as `release-notes/v<version>.md` in the release PR, so the tag contains them.
3. Tag as usual. The run's *Resolve the release notes* step says which file it used.

Notes:

- HTML comments (`<!-- ... -->`) are stripped before publishing - the update dialog shows the body
  as plain text, so a draft note to self must never reach it.
- No file for the tag: the release publishes with an empty body, exactly as before, and the run's
  Summary page carries a warning. Apply the notes by hand right after:
  `gh release edit vX.Y.Z --notes-file <path>`. That still works at any time, with or without a file
  here, and overrides what CI published.

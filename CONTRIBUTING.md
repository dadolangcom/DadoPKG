# Contributing to DadoPKG

## Listing a library

In the library's repository:

    dado package

It writes `dado.json`, tags the release `v<version>`, pushes after asking, and, when the library is not listed yet, offers a pull request here. Answer `y`. With `gh` logged in, it forks this repository, inserts the line and opens the request; without `gh`, it opens GitHub's editor on `README.md` and prints the line and its heading to paste. Non-interactive: `dado package --version=<x.y.z|patch|minor|major> --yes`.

Later releases are only tags. The index stores name and repository, so it never changes for a release.

By hand: add one line to `README.md`, in exactly this form (a space, an em dash U+2014, a space):

    - [Name](https://host/owner/repo) — One-sentence description.

Put it under the `## Category` heading that fits, in its place by name ignoring case. For a new category, add `## Category` after the last one and before `## Unmaintained`, with a blank line around it. A new library never goes under `## Unmaintained`.

## The rules

- The name: letters, digits, `-` and `_`, starting with a letter, at most 64 characters. Not `core`, `app`, `vendor` or `lib`, in any case. Its package form (lowercase, `-` as `_`, what the library's files declare) is not `main` or a Dado keyword (`type`, `for`, `in`, …). Unique ignoring case: `Clipper2` and `clipper2` are one name; and since `my-geo` and `My_Geo` would both be `package my_geo`, they cannot both be listed either. The index keeps your spelling; imports use it lowercased.
- The repository is public: an anonymous `git ls-remote` succeeds. The link is `https://`.
- It has a semver tag, `vX.Y.Z` (a pre-release suffix like `v1.0.0-rc.1` is allowed). `dado get` takes the newest.
- At that tag, `dado.json` is at the repository's root, its `name` is the line's name exactly, and its `repository`, if set, is the line's link.
- No remote dependencies: no `.dado` or `.dados` file imports through `lib:` or `imports:`. A library bundles what it needs and imports it by relative path.
- Its tests pass on the targets `dado.json` claims.
- The description is one sentence.

## What CI checks

On every pull request that changes `README.md`, `scripts/lint_index.py` checks that:

- every line starting `- [` is an entry in the exact form (lines inside a fenced code block, opened by three backticks or tildes, are prose, and a fence must be closed);
- names are valid, not reserved, unique ignoring case, and unique in package form, and links are `https://`;
- entries are sorted within each heading, and none sit above the first heading;
- headings are unique ignoring case, at most 40 characters, not starting with `#`, and `## Unmaintained` is last.

For each line the request adds (a line moved or reworded with the same name and link is not checked again), it also checks the library against the rules above: public, tagged, `dado.json`, no remote imports. Building and running the tests on the claimed targets is not in CI yet.

Run it yourself before opening a request:

    python3 scripts/lint_index.py README.md
    python3 scripts/lint_index.py --added origin/main README.md

## Renaming, removing, moving

Send a pull request from the library's owner:

- Rename: change `name` in `dado.json`, release, then change the line's name (and move it to its sorted place). The old name is freed; code importing `lib:<old>` must change.
- New repository: change the link. CI checks the new one as if it were added.
- Another category: move the line.
- Removal: delete the line.

## Unmaintained

Every listed library is revalidated weekly, and at each Dado release: it must still be public, and its newest tag must still build and pass its tests. A failure opens or updates one issue here, mentioning the author. After three consecutive failed runs, a pull request moves the line under `## Unmaintained`; a later passing run moves it back. `dado get` still fetches an unmaintained library, after a warning.

For now the weekly run checks only that each repository answers and has a semver tag, and opens no issues or requests.

A breaking Dado release also opens an issue on each library it breaks, on the library's own repository. A marker on the index line to opt out of those issues is not designed yet.

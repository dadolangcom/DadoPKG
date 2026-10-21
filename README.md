# DadoPKG

The package index for [Dado](https://github.com/dadolangcom/DadoLang). It maps a library's name to its git repository, and nothing else: git is Dado's package manager, and a release is a tag in the library's own repository.

To fetch a library, `dado get <Name>`. Dado reads this file (from a shallow clone, refreshed about daily), finds the name, ignoring case, and fetches the newest semver tag from the repository it links. Imports use the name in lowercase: `import "lib:clipper2/clip"`.

To list a library, run `dado package` in it and answer `y` when it offers the pull request. See [CONTRIBUTING.md](CONTRIBUTING.md). The libraries are listed below, by category.

The toolchain reads this file. An entry is one line, under a `## Category` heading, sorted by name ignoring case, in exactly this form (a space, an em dash U+2014, a space):

    - [Name](https://host/owner/repo) — One-sentence description.

Every other line is prose and is ignored. `## Unmaintained`, when present, is the last heading: libraries there failed revalidation, and `dado get` warns before fetching one.

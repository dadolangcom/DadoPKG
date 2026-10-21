#!/usr/bin/env python3
"""Lint DadoPKG's README.md, the Dado package index.

    lint_index.py [README.md]                  the file's form and order
    lint_index.py --added BASE [README.md]     that, and every library the
                                               file gains against git ref BASE
    lint_index.py --all-alive [README.md]      that, and every listed
                                               repository answers ls-remote
    lint_index.py --self-test                  built-in cases, no network

An entry is a line in exactly this form, under a `## Category` heading:

    - [Name](https://host/owner/repo) — One-sentence description.

Reading lines is a copy of the toolchain's (tools/dado/library.dado in the
Dado repository: all_lines, fenced, parse_index_line, library_name_refusal
(with name_rule_refusal's package_word and package_word_refusal),
category_refusal, index_categories, index_heading, index_insert,
parse_semver, public_url, same_repository, is_web_url, field_refusal,
index_line_refusal, r24_skips, remote_import, parse_library), so the
lint accepts what `dado get` reads and `dado package` writes. Lines inside a
fence (3+ backticks or tildes, closed only by a line of the same character,
at least as long, alone) are prose, as there. Stricter here: a line starting
`- [` outside a fence that is not an entry is an error (the toolchain passes
over it); the link must be https://; a fence left open is an error (it would hide
every entry after it); every heading but `## Unmaintained` passes
category_refusal; each line is exactly what index_line writes for it (the
round trip index_line_refusal checks, with field_refusal on the link and the
description), so no stray spaces; and two names whose package forms
(lowercase, `-` as `_`) are equal, `my-geo` and `My_Geo`, are refused, since
both libraries would declare the same package.

Python 3 standard library only. Exit 0: clean; 1: the index has errors; 2: the
lint could not run (bad arguments, git failed).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

DASH = " — "  # space, em dash, space
RESERVED = ("core", "app", "vendor", "lib")
# The words the lexer reserves (library.dado's DADO_KEYWORDS, from
# crates/dado_lex): a name whose package form is one cannot be a package.
DADO_KEYWORDS = ("package", "import", "type", "distinct", "const", "enum", "foreign", "view", "for",
                 "in", "if", "else", "return", "break", "continue", "async", "await", "super")
UNMAINTAINED = "Unmaintained"
LIBRARY_KEYS = ("name", "version", "description", "licence", "repository", "targets", "dadoc")


# ── the toolchain's text helpers ────────────────────────────────────────────

def is_space(c):
    # core:strings' trim: four bytes, not Python's whitespace.
    return c in " \t\n\r"


def trim(s):
    a, b = 0, len(s)
    while a < b and is_space(s[a]):
        a += 1
    while b > a and is_space(s[b - 1]):
        b -= 1
    return s[a:b]


def trim_right_cr(s):
    return s[:-1] if s.endswith("\r") else s


def ascii_lower(s):
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in s)


def equal_fold(a, b):
    return ascii_lower(a) == ascii_lower(b)


def is_blank(s):
    return trim(s) == ""


def is_letter(c):
    return ("a" <= c <= "z") or ("A" <= c <= "Z")


def all_lines(text):
    """The text's lines without newlines; a final newline ends the last line."""
    out = []
    rest = text
    while rest:
        i = rest.find("\n")
        if i < 0:
            line, rest, more = rest, "", False
        else:
            line, rest, more = rest[:i], rest[i + 1:], True
        out.append(trim_right_cr(trim_right_cr(line)))
        if not more:
            break
    return out


def is_heading(line):
    return line.startswith("## ")


def heading_name(line):
    return trim(line[3:])


def fence_run(t):
    """How many of t's first character, a backtick or ~, it starts with."""
    if t == "" or t[0] not in "`~":
        return 0
    n = 0
    while n < len(t) and t[n] == t[0]:
        n += 1
    return n


def fence_scan(lines):
    """(fenced, open_at): which lines are inside a fenced code block, fence
    lines included, and the 1-based line of a fence left open (0 if none).
    A fence is 3 or more backticks or tildes; it closes only at a line of
    only that character, at least as many of it as opened it."""
    out = []
    opened, width, at = "", 0, 0
    for n, l in enumerate(lines, 1):
        t = trim(l)
        run = fence_run(t)
        if width == 0:
            if run >= 3:
                opened, width, at = t[0], run, n
            out.append(run >= 3)
            continue
        if run >= width and t[0] == opened and run == len(t):
            width = 0
        out.append(True)
    return out, (at if width else 0)


def fenced(lines):
    return fence_scan(lines)[0]


def unclosed_fence(lines):
    return fence_scan(lines)[1]


def category_refusal(category):
    """Why `category` cannot head a new listing, or ""."""
    c = trim(category)
    if c == "":
        return "the category is empty"
    if equal_fold(c, UNMAINTAINED):
        return ("`Unmaintained` is where the index's maintainers move abandoned libraries; "
                "a new listing goes under another heading")
    if len(c.encode("utf-8", "surrogateescape")) > 40:
        return "`%s` is longer than 40 characters" % c
    if "\n" in c or c.startswith("#"):
        return "`%s` is not a heading's text" % c
    return ""


def index_categories(index):
    """The `## ` headings outside fences, in order, but Unmaintained."""
    lines = all_lines(index)
    code = fenced(lines)
    return [heading_name(l) for i, l in enumerate(lines)
            if not code[i] and is_heading(l) and not equal_fold(heading_name(l), UNMAINTAINED)]


def index_heading(index, category):
    """`category` as the index already spells it, or trimmed when new."""
    c = trim(category)
    for h in index_categories(index):
        if equal_fold(h, c):
            return h
    return c


# ── names and lines ─────────────────────────────────────────────────────────

def library_name_refusal(name):
    """Why `name` cannot name a library, or ""."""
    if name == "":
        return "the name is empty"
    if len(name.encode("utf-8", "surrogateescape")) > 64:
        return "`%s` is longer than 64 characters" % name
    if not is_letter(name[0]):
        return "`%s` must start with a letter" % name
    for c in name:
        if not (is_letter(c) or "0" <= c <= "9" or c in "-_"):
            return "`%s` holds `%s`; a name is letters, digits, `-` and `_`" % (name, c)
    why = package_word_refusal(name)
    if why:
        return why
    if ascii_lower(name) in RESERVED:
        return "`%s` is a standard collection's name" % name
    return ""


def package_word(name):
    """The package name `name` is written as: lowercase, `-` as `_`."""
    return ascii_lower(name).replace("-", "_")


def package_word_refusal(name):
    """Why `name`'s package form cannot name a library's package, or ""."""
    word = package_word(name)
    if word == "main":
        return "`%s` would be `package main`, which is a program's package" % name
    if word in DADO_KEYWORDS:
        return "`%s` would be `package %s`, and `%s` is a Dado keyword" % (name, word, word)
    return ""


class Entry(object):
    def __init__(self, name, url, description, lineno=0, category=None):
        self.name = name
        self.url = url
        self.description = description
        self.lineno = lineno
        self.category = category
        self.line = ""


def parse_index_line(line):
    """(Entry, "") for a line in the exact form, else (None, why).

    `why` is "" for a line that does not start `- [` (prose to everyone)."""
    l = trim_right_cr(line)
    if not l.startswith("- ["):
        return None, ""
    body = l[3:]
    i = body.find("](")
    if i < 0:
        return None, "the name is not followed by `](`"
    name, rest = body[:i], body[i + 2:]
    if name == "":
        return None, "the name is empty"
    j = rest.find(")")
    if j < 0:
        return None, "the link is not closed with `)`"
    url, tail = rest[:j], rest[j + 1:]
    if url == "":
        return None, "the link is empty"
    if " " in url:
        return None, "the link holds a space (or a `)` ends it early)"
    if not tail.startswith(DASH):
        return None, "after the link comes ` — ` (space, em dash U+2014, space)" + dash_hint(tail)
    d = tail[len(DASH):]
    if trim(d) == "":
        return None, "the description is empty"
    why = library_name_refusal(name)
    if why:
        return None, why
    return Entry(name, url, d), ""


def dash_hint(tail):
    t = tail.lstrip(" ")
    if tail == "":
        return "; the line ends at the link"
    if t.startswith("–"):
        return "; this is an en dash (U+2013)"
    if t.startswith("--") or t.startswith("-"):
        return "; this is a hyphen"
    if tail.startswith("—") or tail.startswith("  —") or t.startswith("—"):
        return "; check the spaces around it"
    return ""


def index_line(name, url, description):
    return "- [" + name + "](" + url + ")" + DASH + trim(description)


def field_refusal(key, value):
    """Why `value` cannot be dado.json's `key`, or "": no control character
    in any field; `repository` also no space, ()[]<> or backtick."""
    for c in value:
        if ord(c) < 0x20 or ord(c) == 0x7f:
            return "`%s` holds a control character (byte %d): write it on one line, without tabs" % (key, ord(c))
    if key != "repository":
        return ""
    for c in value:
        if c in " ()[]<>`":
            return ("`repository` holds `%s`, which a URL in the index cannot: write the address users clone, "
                    "like https://github.com/owner/name" % c)
    return ""


def index_line_refusal(name, url, description):
    """Why the line for these three would not read back as the same three, or ""."""
    d = trim(description)
    if d == "":
        return "the description is empty"
    e, _ = parse_index_line(index_line(name, url, description))
    if e is None or e.name != name or e.url != url or trim(e.description) != d:
        return ("the index line for `%s` (%s) would not read back as written: keep `name` to letters, digits, "
                "`-` and `_`, and `repository` to a plain https:// address" % (name, url))
    return ""


HTTPS_URL = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]+)?/[^\s]+$")


def url_refusal(url):
    if not HTTPS_URL.match(url):
        return "the link `%s` is not an https:// repository URL" % url
    return ""


# ── the index as a whole ────────────────────────────────────────────────────

def read_index(text):
    """(entries, headings, malformed): entries with line numbers and the
    heading they are under (None before the first); headings as
    (lineno, name); malformed as (lineno, why) for `- [` lines that are not
    entries."""
    entries, headings, malformed = [], [], []
    category = None
    lines = all_lines(text)
    code = fenced(lines)
    for n, l in enumerate(lines, 1):
        if code[n - 1]:
            continue
        if is_heading(l):
            category = heading_name(l)
            headings.append((n, category))
            continue
        e, why = parse_index_line(l)
        if e is None:
            if why:
                malformed.append((n, why))
            continue
        e.lineno = n
        e.category = category
        e.line = trim_right_cr(l)
        entries.append(e)
    return entries, headings, malformed


def lint_text(text):
    """Every error in the index, as (lineno, message), in line order."""
    errors = []
    entries, headings, malformed = read_index(text)
    errors.extend(malformed)

    fence = unclosed_fence(all_lines(text))
    if fence:
        errors.append((fence, "this code fence is never closed, so everything after it is prose"))

    seen = {}
    for n, h in headings:
        if h == "":
            errors.append((n, "the heading has no name"))
            continue
        why = category_refusal(h)
        if why and not equal_fold(h, UNMAINTAINED):
            errors.append((n, why))
        key = ascii_lower(h)
        if key in seen:
            errors.append((n, "the heading `## %s` is here already, on line %d" % (h, seen[key])))
        else:
            seen[key] = n
    for i, (n, h) in enumerate(headings):
        if equal_fold(h, UNMAINTAINED) and i != len(headings) - 1:
            errors.append((n, "`## %s` must be the last heading" % h))

    names = {}
    packages = {}
    prev = None
    for e in entries:
        if e.category is None:
            errors.append((e.lineno, "`%s` is above the first `## ` heading; entries sit under a category" % e.name))
        for why in (url_refusal(e.url), field_refusal("repository", e.url),
                    field_refusal("description", e.description),
                    index_line_refusal(e.name, e.url, e.description)):
            if why:
                errors.append((e.lineno, why))
                break
        else:
            if e.line != index_line(e.name, e.url, e.description):
                errors.append((e.lineno, "the line is not exactly as `dado package` writes it: one space after "
                               "the em dash, none at the end"))
        key = ascii_lower(e.name)
        if key in names:
            first = names[key]
            errors.append((e.lineno, "`%s` is listed already on line %d (as `%s`); names are unique ignoring case"
                           % (e.name, first.lineno, first.name)))
        else:
            names[key] = e
            # Stricter than the toolchain: two names one package form apart
            # (`my-geo`, `My_Geo`) would both declare `package my_geo`.
            word = package_word(e.name)
            other = packages.get(word)
            if other is not None:
                errors.append((e.lineno, "`%s` and `%s` (line %d) would both be `package %s` (lowercase, `-` as "
                               "`_`); choose a name that differs in more than case, `-` and `_`"
                               % (e.name, other.name, other.lineno, word)))
            else:
                packages[word] = e
        if prev is not None and prev.category == e.category and ascii_lower(prev.name) > key:
            errors.append((e.lineno, "`%s` is out of order: it sorts before `%s` (line %d), ignoring case"
                           % (e.name, prev.name, prev.lineno)))
        prev = e
    errors.sort(key=lambda x: x[0])
    return errors


# ── index_insert, as `dado package` writes a new line ───────────────────────

def join_lines_with(lines, at, extra):
    out = []
    for i, l in enumerate(lines):
        if i == at:
            out.extend(x + "\n" for x in extra)
        out.append(l + "\n")
    if at >= len(lines):
        out.extend(x + "\n" for x in extra)
    return "".join(out)


def index_insert(index, category, name, line):
    """`index` with `line` added under `## category` in sorted place; a new
    heading goes after the last, before `## Unmaintained`."""
    lines = all_lines(index)
    code = fenced(lines)
    heading = -1
    for i, l in enumerate(lines):
        if not code[i] and is_heading(l) and equal_fold(heading_name(l), category):
            heading = i
            break
    if heading >= 0:
        at = -1
        last_entry = heading
        for i in range(heading + 1, len(lines)):
            l = lines[i]
            if code[i]:
                continue
            if is_heading(l):
                break
            e, _ = parse_index_line(l)
            if e is None:
                continue
            if ascii_lower(e.name) > ascii_lower(name):
                at = i
                break
            last_entry = i
        if at < 0:
            at = last_entry + 1
            if last_entry == heading:
                at = heading + 1
                if at < len(lines) and is_blank(lines[at]):
                    at += 1
                if at < len(lines) and not code[at] and is_heading(lines[at]):
                    return join_lines_with(lines, at, [line, ""])
        return join_lines_with(lines, at, [line])
    before = len(lines)
    for i, l in enumerate(lines):
        if not code[i] and is_heading(l) and equal_fold(heading_name(l), UNMAINTAINED):
            before = i
            break
    cut = before
    while cut > 0 and is_blank(lines[cut - 1]):
        cut -= 1
    section = [""] if cut > 0 else []
    section += ["## " + category, "", line]
    if before < len(lines):
        section.append("")
    head = lines[:cut] + section + lines[before:]
    return join_lines_with(head, len(head), [])


# ── versions, URLs, manifests, imports ──────────────────────────────────────

I64_MAX = (1 << 63) - 1


def parse_semver(s):
    """(major, minor, patch, pre) or None; a leading v or V is allowed."""
    t = s[1:] if s[:1] in ("v", "V") else s
    core, sep, pre = t.partition("-")
    if sep:
        if pre == "":
            return None
        for c in pre:
            if not (is_letter(c) or "0" <= c <= "9" or c in ".-"):
                return None
    parts = core.split(".")
    if len(parts) != 3:
        return None
    n = []
    for p in parts:
        if p == "" or (len(p) > 1 and p[0] == "0"):
            return None
        if not all("0" <= c <= "9" for c in p):
            return None
        v = int(p)
        if v > I64_MAX:
            return None
        n.append(v)
    return (n[0], n[1], n[2], pre)


def all_digits(s):
    return s != "" and all("0" <= c <= "9" for c in s)


def semver_less(a, b):
    if a[:3] != b[:3]:
        return a[:3] < b[:3]
    if a[3] == b[3]:
        return False
    if a[3] == "":
        return False
    if b[3] == "":
        return True
    x, y = a[3].split("."), b[3].split(".")
    for i in range(min(len(x), len(y))):
        if x[i] == y[i]:
            continue
        xn, yn = all_digits(x[i]), all_digits(y[i])
        if xn and yn:
            if len(x[i]) != len(y[i]):
                return len(x[i]) < len(y[i])
            return x[i] < y[i]
        if xn != yn:
            return xn
        return x[i] < y[i]
    return len(x) < len(y)


def newest_semver_tag(tags):
    """The newest semver tag among `tags`, as written (with its v), or ""."""
    best, best_v = "", None
    for t in tags:
        v = parse_semver(trim(t))
        if v is None:
            continue
        if best_v is None or semver_less(best_v, v):
            best, best_v = trim(t), v
    return best


def public_url(remote):
    u = trim(remote)
    if u.startswith("ssh://"):
        u = u[6:]
        user, at, after = u.partition("@")
        if at:
            u = after
        u = "https://" + u
    elif "://" not in u:
        # git's rule: `host:path` is the scp form only when no `/` comes
        # before the colon, so `/srv/r.git` and `C:\\r` are paths.
        user_host, colon, path = u.partition(":")
        if not colon or path == "" or len(user_host) < 2 or "/" in user_host or "\\" in user_host:
            return u[:-1] if u.endswith("/") else u
        user, at, host = user_host.partition("@")
        if not at:
            host = user_host
        u = "https://" + host + "/" + path
    if u.endswith("/"):
        u = u[:-1]
    if is_web_url(u) and u.endswith(".git"):
        u = u[:-4]
    return u


def same_repository(a, b):
    """Two addresses name one repository: equal public_urls, ignoring case."""
    return equal_fold(public_url(a), public_url(b))


def is_web_url(url):
    """An http(s) address with a host."""
    if url.startswith("https://"):
        rest = url[8:]
    elif url.startswith("http://"):
        rest = url[7:]
    else:
        return False
    return rest.partition("/")[0] != ""


def _no_constants(name):
    raise ValueError("%s is not JSON" % name)


def parse_library(text):
    """(dict, "") for dado.json's text as the toolchain takes it, else
    (None, why)."""
    if trim(text) == "":
        return {}, ""
    try:
        top = json.loads(text, parse_constant=_no_constants)
    except ValueError as e:
        return None, "is not JSON: %s" % e
    if not isinstance(top, dict):
        return None, "is not a JSON object: write { ... } around its keys"
    for k, v in top.items():
        if k == "license":
            return None, "has the key `license`: the manifest spells it `licence`"
        if k not in LIBRARY_KEYS:
            return None, "has the key `%s`, which is not one of: %s" % (k, " ".join(LIBRARY_KEYS))
        if k == "targets":
            if not isinstance(v, list) or not all(isinstance(t, str) for t in v):
                return None, '`targets` is not an array of strings, e.g. ["native", "web"]'
            for t in v:
                why = field_refusal("targets", t)
                if why:
                    return None, why
        elif not isinstance(v, str):
            return None, "`%s` is not a string" % k
        else:
            why = field_refusal(k, v)
            if why:
                return None, why
    version = top.get("version", "")
    if version and parse_semver(version) is None:
        return None, "`version` is `%s`, which is not a semver version like 1.4.2" % version
    return top, ""


def r24_skips(rel):
    """R24's scan passes over the root's build/ and anything under a .git."""
    parts = rel.replace("\\", "/").split("/")
    if len(parts) > 1 and parts[0] == "build":
        return True
    return ".git" in parts


def remote_import(source):
    """(lineno, line) of the first import through lib: or imports:, or (0, "")."""
    for n, raw in enumerate(all_lines(source), 1):
        l = trim(raw)
        if not l.startswith("import"):
            continue
        if '"lib:' in l or '"imports:' in l:
            return n, l
    return 0, ""


# ── git, anonymously ────────────────────────────────────────────────────────

def git_env():
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    env.pop("GIT_ASKPASS", None)
    env.pop("SSH_ASKPASS", None)
    return env


# No credentials, no prompt, https only (no file://, ext:: or ssh).
GIT_ANON = ["git", "-c", "credential.helper=", "-c", "core.askPass=true",
            "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
            "-c", "core.symlinks=false"]


def run(cmd, timeout, cwd=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, env=git_env(), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ds" % timeout
    except OSError as e:
        return 127, "", str(e)
    return (p.returncode, p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def first_line(s):
    for l in s.splitlines():
        if l.strip():
            return l.strip()
    return ""


def remote_tags(url):
    """(tags, why): the repository's tag names, or why it cannot be read."""
    code, out, err = run(GIT_ANON + ["ls-remote", "--tags", "--refs", url], 90)
    if code != 0:
        return None, "`git ls-remote --tags %s` failed without signing in (%s); is it public?" % (
            url, first_line(err) or "exit %d" % code)
    tags = []
    for l in out.splitlines():
        _, _, ref = l.partition("\t")
        if ref.startswith("refs/tags/"):
            tags.append(ref[len("refs/tags/"):])
    return tags, ""


def check_library(e):
    """Errors for one added entry: reachable, tagged, manifest, no remote imports."""
    why = url_refusal(e.url)
    if why:
        return [why]
    tags, why = remote_tags(e.url)
    if tags is None:
        return [why]
    tag = newest_semver_tag(tags)
    if not tag:
        return ["%s has no semver tag (like v1.0.0); `dado package` tags each release" % e.url]
    work = tempfile.mkdtemp(prefix="dadopkg-")
    try:
        dest = os.path.join(work, "lib")
        code, _, err = run(GIT_ANON + ["clone", "-q", "--depth", "1", "--no-tags", "--single-branch",
                                       "--branch", tag, "--", e.url, dest], 300)
        if code != 0:
            return ["could not clone %s at %s (%s)" % (e.url, tag, first_line(err) or "exit %d" % code)]
        return check_tree(e, dest, tag)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check_tree(e, root, tag):
    errors = []
    at = "%s at %s" % (e.url, tag)
    manifest = os.path.join(root, "dado.json")
    if not os.path.isfile(manifest) or os.path.islink(manifest):
        errors.append("%s has no dado.json at its root" % at)
    else:
        with open(manifest, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        lib, why = parse_library(text)
        if lib is None:
            errors.append("%s: dado.json %s" % (at, why))
        else:
            name = lib.get("name", "")
            if name != e.name:
                errors.append("%s: dado.json's name is `%s`, but the index line says `%s` (they must match exactly)"
                              % (at, name, e.name))
            repo = lib.get("repository", "")
            if repo and not same_repository(repo, e.url):
                errors.append("%s: dado.json's repository is %s, but the index line links %s" % (at, repo, e.url))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for fn in sorted(filenames):
            if not (fn.endswith(".dado") or fn.endswith(".dados")):
                continue
            path = os.path.join(dirpath, fn)
            if os.path.islink(path) or r24_skips(os.path.relpath(path, root)):
                continue
            with open(path, "rb") as f:
                src = f.read().decode("utf-8", "replace")
            n, l = remote_import(src)
            if n:
                rel = os.path.relpath(path, root)
                errors.append("%s: %s:%d imports another library (%s); a library bundles what it needs "
                              "and imports it by relative path" % (at, rel, n, l))
    return errors


# ── the modes ───────────────────────────────────────────────────────────────

def git_in(path, args):
    d = os.path.dirname(os.path.abspath(path))
    try:
        p = subprocess.run(["git"] + args, cwd=d, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        return 127, "", str(e)
    return p.returncode, p.stdout.decode("utf-8", "surrogateescape"), p.stderr.decode("utf-8", "replace")


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_line_numbers(path, base):
    """Line numbers of `path` added since `base`, or None when git failed."""
    name = os.path.basename(path)
    code, out, err = git_in(path, ["diff", "--no-color", "--no-ext-diff", "--unified=0", base, "--", name])
    if code != 0:
        sys.stderr.write("lint_index: git diff %s failed: %s\n" % (base, first_line(err)))
        return None
    added = set()
    n = 0
    for l in out.split("\n"):
        m = HUNK.match(l)
        if m:
            n = int(m.group(1))
            continue
        if l.startswith("+") and not l.startswith("+++"):
            added.add(n)
            n += 1
    return added


def base_entries(path, base):
    code, out, _ = git_in(path, ["show", "%s:./%s" % (base, os.path.basename(path))])
    if code != 0:
        return {}
    entries, _, _ = read_index(out)
    return dict((ascii_lower(e.name), e) for e in entries)


def report(path, errors):
    for n, msg in errors:
        print("%s:%d: %s" % (path, n, msg))


def mode_added(path, text, base):
    added = added_line_numbers(path, base)
    if added is None:
        return 2
    before = base_entries(path, base)
    entries, _, _ = read_index(text)
    errors = []
    checked = 0
    for e in entries:
        if e.lineno not in added:
            continue
        old = before.get(ascii_lower(e.name))
        if old is not None and old.name == e.name and same_repository(old.url, e.url):
            print("%s:%d: %s moved or reworded; its repository is unchanged, so not checked again"
                  % (path, e.lineno, e.name))
            continue
        if old is None and equal_fold(e.category or "", UNMAINTAINED):
            errors.append((e.lineno, "`%s` is new; a new library is not listed under `## %s`" % (e.name, UNMAINTAINED)))
            continue
        checked += 1
        print("%s:%d: checking %s (%s)" % (path, e.lineno, e.name, e.url))
        sys.stdout.flush()
        for msg in check_library(e):
            errors.append((e.lineno, msg))
    report(path, errors)
    if errors:
        return 1
    print("%s: %d added %s checked" % (path, checked, "library" if checked == 1 else "libraries"))
    return 0


def mode_all_alive(path, text):
    entries, _, _ = read_index(text)
    dead = 0
    for e in entries:
        tags, why = remote_tags(e.url) if not url_refusal(e.url) else (None, url_refusal(e.url))
        if tags is not None and not newest_semver_tag(tags):
            why, tags = "no semver tag", None
        if tags is None:
            dead += 1
            print("%s:%d: %s: %s" % (path, e.lineno, e.name, why))
        else:
            print("%s:%d: %s: alive, newest %s" % (path, e.lineno, e.name, newest_semver_tag(tags)))
        sys.stdout.flush()
    print("%s: %d of %d alive" % (path, len(entries) - dead, len(entries)))
    return 1 if dead else 0


# ── self-test ───────────────────────────────────────────────────────────────

GOOD = (
    "# DadoPKG\n\nIntro prose, with a [link](https://example.com) — not an entry.\n\n"
    "    - [Name](https://host/owner/repo) — an indented example is prose.\n\n"
    "```md\n## Example\n\n- [Name](https://host/owner/repo) — a fenced example is prose.\n"
    "- [bad line, also prose](x)\n```\n\n"
    "~~~\n- [Tilde](https://a/t) — In a fence.\n~~~\n\n"
    "## Geometry\n\n"
    "- [Clipper2](https://github.com/o/clipper2) — Polygon clipping.\n"
    "- [earcut](https://github.com/o/earcut) — Ears.\n"
    "Prose between entries is ignored.\n"
    "- [Tess](https://codeberg.org/o/tess.git) — Tessellate.\n\n"
    "### A subheading is prose\n\n"
    "- [Zed](https://a.example/z) — Z.\n\n"
    "## Audio\n\n"
    "- [Ogg](https://github.com/o/ogg) — Vorbis.\n\n"
    "## Unmaintained\n\n"
    "- [Old](https://github.com/o/old) — Old.\n"
)


def self_test():
    failures = []

    def expect(cond, what):
        if not cond:
            failures.append(what)

    def errs(text):
        return lint_text(text)

    def has_error(text, needle, what):
        e = errs(text)
        expect(any(needle in m for _, m in e), "%s: wanted an error with `%s`, got %r" % (what, needle, e))

    # The good file is clean, and reads as the toolchain reads it.
    expect(errs(GOOD) == [], "the good file: %r" % errs(GOOD))
    entries, headings, _ = read_index(GOOD)
    expect([e.name for e in entries] == ["Clipper2", "earcut", "Tess", "Zed", "Ogg", "Old"],
           "the good file's entries: %r" % [e.name for e in entries])
    expect([h for _, h in headings] == ["Geometry", "Audio", "Unmaintained"], "headings %r" % headings)
    expect(entries[3].category == "Geometry", "Zed's category is %r" % entries[3].category)
    expect(index_categories(GOOD) == ["Geometry", "Audio"], "index_categories %r" % index_categories(GOOD))
    expect(errs("") == [], "an empty file")
    expect(errs("# DadoPKG\r\n\r\n## A\r\n\r\n- [X](https://h/o/x) — d.\r\n") == [], "CRLF")

    # library.dado's own cases.
    line = index_line("Clipper2", "https://github.com/o/clipper2", "Polygon clipping.")
    expect(line == "- [Clipper2](https://github.com/o/clipper2) — Polygon clipping.", "index_line: " + line)
    e, _ = parse_index_line(line)
    expect(e is not None and e.name == "Clipper2" and e.url == "https://github.com/o/clipper2"
           and e.description == "Polygon clipping.", "the line read back")
    for bad in ["- [X](u)", "- [X](a b) — d", "* [X](u) — d", "- [](u) — d", "- [2x](u) — d",
                "- [X](u) - d", "- [X](u) — "]:
        e, _ = parse_index_line(bad)
        expect(e is None, "`%s` was taken as an entry" % bad)
    for good in ["Clipper2", "libtess2", "a", "my-lib_2", "a" * 64]:
        expect(library_name_refusal(good) == "", "%s was refused" % good)
    for bad in ["", "2d", "-x", "a b", "a/b", "a.b", "core", "Vendor", "LIB", "App", "a" * 65, "café",
                "Type", "for", "In", "super", "main", "Main", "Async", "distinct"]:
        expect(library_name_refusal(bad) != "", "`%s` was taken" % bad)
    # new_test.dado's cases: the package form, not the spelling, is checked.
    expect("keyword" in library_name_refusal("Type"), "Type's refusal: %r" % library_name_refusal("Type"))
    expect("program's package" in library_name_refusal("main"), "main's refusal")
    for good in ["types", "my-for", "Main2", "try", "nil", "i32", "my-geo"]:
        expect(library_name_refusal(good) == "", "%s was refused: %r" % (good, library_name_refusal(good)))
    expect(package_word("My-Geo") == "my_geo", "package_word")

    idx = ("# DadoPKG\n\nIntro prose.\n\n## Geometry\n\n- [Clipper2](https://a/c) — Clip.\n"
           "- [Tess](https://a/t) — Tessellate.\n\n## Unmaintained\n\n- [Old](https://a/o) — Old.\n")
    mid = index_insert(idx, "geometry", "Earcut", "- [Earcut](https://a/e) — Ears.")
    expect("- [Clipper2](https://a/c) — Clip.\n- [Earcut](https://a/e) — Ears.\n- [Tess]" in mid, "middle insert")
    first = index_insert(idx, "Geometry", "Arc", "- [Arc](https://a/a) — A.")
    expect("## Geometry\n\n- [Arc](https://a/a) — A.\n- [Clipper2]" in first, "first insert")
    last = index_insert(idx, "Geometry", "Zed", "- [Zed](https://a/z) — Z.")
    expect("- [Tess](https://a/t) — Tessellate.\n- [Zed](https://a/z) — Z.\n\n## Unmaintained" in last,
           "last insert")
    fresh = index_insert(idx, "Audio", "Ogg", "- [Ogg](https://a/g) — Vorbis.")
    expect("- [Tess](https://a/t) — Tessellate.\n\n## Audio\n\n- [Ogg](https://a/g) — Vorbis.\n\n"
           "## Unmaintained\n" in fresh, "new section before Unmaintained")
    bare = index_insert("# DadoPKG\n\nProse.\n", "Audio", "Ogg", "- [Ogg](https://a/g) — Vorbis.")
    expect(bare == "# DadoPKG\n\nProse.\n\n## Audio\n\n- [Ogg](https://a/g) — Vorbis.\n", "bare: %r" % bare)
    empty = index_insert("# I\n\n## Audio\n\n## Geometry\n", "Audio", "Ogg", "- [Ogg](https://a/g) — V.")
    expect(empty == "# I\n\n## Audio\n\n- [Ogg](https://a/g) — V.\n\n## Geometry\n", "empty: %r" % empty)
    # Whatever index_insert writes into a clean index stays clean.
    grown = GOOD
    for cat, name in [("Geometry", "Arc"), ("geometry", "Mesh"), ("Audio", "Flac"), ("Text", "Utf"), ("Net", "Http")]:
        grown = index_insert(grown, cat, name, index_line(name, "https://github.com/o/" + name.lower(), "D."))
        expect(errs(grown) == [], "after inserting %s: %r" % (name, errs(grown)))

    # Fences, as library_test.dado has them.
    fence = "```"
    example = fence + "md\n## Example\n\n- [Fake](https://a/f) — In a fence.\n" + fence + "\n"
    tilde = "~~~\n- [Tilde](https://a/t) — In a fence.\n~~~\n"
    doc = ("# DadoPKG\n\nEach line looks like:\n\n" + example + "\n" + tilde +
           "\n## Geometry\n\n- [Tess](https://a/t) — Tessellate.\n")
    fe, _, _ = read_index(doc)
    expect([(e.name, e.category) for e in fe] == [("Tess", "Geometry")], "fenced entries: %r" % [e.name for e in fe])
    expect(index_categories(doc) == ["Geometry"], "fenced headings: %r" % index_categories(doc))
    expect(errs(doc) == [], "the fenced doc: %r" % errs(doc))
    into = index_insert(doc, "Geometry", "Arc", "- [Arc](https://a/a) — A.")
    expect(example in into and "## Geometry\n\n- [Arc](https://a/a) — A.\n- [Tess]" in into, "insert beside a fence")
    ex_cat = index_insert(doc, "Example", "Arc", "- [Arc](https://a/a) — A.")
    expect(example in ex_cat and "- [Tess](https://a/t) — Tessellate.\n\n## Example\n\n- [Arc]" in ex_cat,
           "a fenced heading taken as the section: %r" % ex_cat)
    expect(errs(ex_cat) == [], "after the Example insert: %r" % errs(ex_cat))

    # A fence closes only on its own character, at least as long, alone.
    nested = ("# I\n\n````\n```\n## Inner\n```\n- [In](https://a/i) — In.\n````\n\n~~~\n```\n"
              "- [Mixed](https://a/m) — M.\n~~~\n\n```\n```js\n- [Info](https://a/n) — N.\n```\n\n"
              "## Real\n\n- [Out](https://a/o) — Out.\n")
    ne, _, _ = read_index(nested)
    expect([e.name for e in ne] == ["Out"], "nested fences read %r" % [e.name for e in ne])
    expect(index_categories(nested) == ["Real"], "a heading in a nested fence: %r" % index_categories(nested))
    expect(errs(nested) == [], "the nested doc: %r" % errs(nested))
    expect(unclosed_fence(all_lines("````\n```\n")) == 1, "a 4-fence closed by 3")
    expect(unclosed_fence(all_lines("```\n~~~\n")) == 1, "a backtick fence closed by tildes")
    expect(unclosed_fence(all_lines("```\n``` x\n")) == 1, "a fence closed by a line with more on it")
    expect(unclosed_fence(all_lines("```\n `````  \n")) == 0, "a longer, indented closing fence")

    # Round trips and fields.
    expect(index_line_refusal("Geo", "https://h/o/geo", " Areas — and more. ") == "", "a good line refused")
    for bad in ["https://h/o/(geo)", "https://h/o geo", ""]:
        expect(index_line_refusal("Geo", bad, "Areas.") != "", "the url %r round-tripped" % bad)
    expect(index_line_refusal("Geo", "https://h/o/geo", "  ") != "", "an empty description round-tripped")
    expect(field_refusal("description", "tab\there") != "", "a tab in a description")
    expect(field_refusal("description", "Fine — with a dash.") == "", "a dash in a description")
    expect(same_repository("git@github.com:Owner/Geo.git", "https://github.com/owner/geo/"), "one repo, two spellings")
    expect(not same_repository("https://github.com/a/geo", "https://github.com/b/geo"), "two owners as one")
    for skipped in ["build/x.dado", "build\\c\\x.dado", ".git/x.dado", "src/.git/hooks/x.dado"]:
        expect(r24_skips(skipped), "%s was scanned" % skipped)
    for scanned in ["geo.dado", "src/build/x.dado", "src\\build\\x.dado", "builds/x.dado", "build.dado", "x.git/y.dado"]:
        expect(not r24_skips(scanned), "%s was skipped" % scanned)

    # Categories.
    for good in ["Geometry", "Audio and music"]:
        expect(category_refusal(good) == "", "the category %s was refused" % good)
    for bad in ["", "  ", "Unmaintained", "unMAINTAINED", "## Geo", "a\nb",
                "a category name far longer than forty characters"]:
        expect(category_refusal(bad) != "", "the category %r was taken" % bad)
    expect(index_heading(idx, " geometry ") == "Geometry", "index_heading, existing")
    expect(index_heading(idx, " Audio ") == "Audio", "index_heading, new")
    expect(index_categories(idx) == ["Geometry"], "index_categories(idx)")

    # Each error kind.
    base = "# I\n\n## A\n\n"
    has_error(base + "- [X](https://h/o/x)\n", "em dash", "no description")
    has_error(base + "- [X](https://h/o/x) - d\n", "hyphen", "a hyphen")
    has_error(base + "- [X](https://h/o/x) – d\n", "en dash", "an en dash")
    has_error(base + "- [X](https://h/o/x) —  \n", "description is empty", "a blank description")
    has_error(base + "- [X] (https://h/o/x) — d\n", "`](`", "a space before the link")
    has_error(base + "- [X](https://h/o/x — d\n", "`)`", "an unclosed link")
    has_error(base + "- [X](https://h/o/a b) — d\n", "space", "a space in the link")
    has_error(base + "- [](https://h/o/x) — d\n", "name is empty", "an empty name")
    has_error(base + "- [2d](https://h/o/x) — d\n", "start with a letter", "a digit first")
    has_error(base + "- [a.b](https://h/o/x) — d\n", "letters, digits", "a dot")
    has_error(base + "- [Core](https://h/o/x) — d\n", "standard collection", "a reserved name")
    has_error(base + "- [%s](https://h/o/x) — d\n" % ("a" * 65), "longer than 64", "a long name")
    has_error(base + "- [X](http://h/o/x) — d\n", "https://", "http")
    has_error(base + "- [X](./x) — d\n", "https://", "a path")
    has_error(base + "- [X](https://h/o/x) — d\n- [x](https://h/o/y) — d\n", "unique ignoring case",
              "a duplicate")
    has_error(base + "- [X](https://h/o/x) — d\n\n## B\n\n- [x](https://h/o/y) — d\n",
              "unique ignoring case", "a duplicate across headings")
    has_error(base + "- [b](https://h/o/b) — d\n- [A](https://h/o/a) — d\n", "out of order", "unsorted")
    has_error(base + "- [Type](https://h/o/x) — d\n", "keyword", "a keyword's package form")
    has_error(base + "- [Main](https://h/o/x) — d\n", "package main", "main")
    has_error(base + "- [my-geo](https://h/o/a) — d\n- [my_geo](https://h/o/b) — d\n", "both be `package my_geo`",
              "a package-form collision")
    has_error(base + "- [My_Geo](https://h/o/a) — d\n\n## B\n\n- [my-geo](https://h/o/b) — d\n",
              "both be `package my_geo`", "a package-form collision across headings, case apart")
    expect(len(errs(base + "- [X](https://h/o/x) — d\n- [x](https://h/o/y) — d\n")) == 1,
           "a duplicate is one error, not also a collision")
    has_error("# I\n\n- [X](https://h/o/x) — d\n\n## A\n", "first `## ` heading", "an entry before a heading")
    has_error(base + "## Unmaintained\n\n## B\n", "last heading", "Unmaintained not last")
    has_error(base + "## unmaintained\n\n## B\n", "last heading", "unmaintained, lowercase, not last")
    has_error(base + "## a\n", "is here already", "a duplicate heading")
    has_error(base + "## \n", "no name", "an empty heading")
    has_error(base + "- [X](https://h/o/x) — d.  \n", "exactly as", "trailing spaces")
    has_error(base + "- [X](https://h/o/x) —  d.\n", "exactly as", "two spaces after the dash")
    has_error(base + "- [X](https://h/o/<x>) — d.\n", "`<`", "a < in the link")
    has_error(base + "- [X](https://h/o/x) — a\tb.\n", "control character", "a tab in the description")
    has_error(base + "## #Geo\n", "not a heading's text", "a heading starting #")
    has_error(base + "## " + "x" * 41 + "\n", "longer than 40", "a long heading")
    has_error(base + "```\n- [X](https://h/o/x) — d\n", "never closed", "an unclosed fence")
    expect(errs(base + "```\n- [X](u) - d\n## \n```\n") == [], "a malformed line and heading in a fence")
    expect(errs(base + "- [b](https://h/o/b) — d\n\n## B\n\n- [A](https://h/o/a) — d\n") == [],
           "sorting restarts under each heading")
    expect(errs(base + "* [X](u) - d\n  - [X](u)\n-[X](u)\n") == [], "lines not starting `- [` are prose")

    # Versions, URLs, manifests, imports.
    expect(parse_semver("v1.20.3-rc.1") == (1, 20, 3, "rc.1"), "semver with v and pre")
    for bad in ["1.2", "1.2.3.4", "01.2.3", "1.2.x", "1.2.3-", "", "v", "1..3", "1.2.3-a b", "1.2.3+b"]:
        expect(parse_semver(bad) is None, "`%s` read as semver" % bad)
    expect(newest_semver_tag(["v1.2.0", "v1.10.0", "latest", "v1.10.0-rc.1", "v0.9.9"]) == "v1.10.0", "newest tag")
    expect(newest_semver_tag(["v2.0.0-rc.2", "v2.0.0-rc.10", "v1.0.0"]) == "v2.0.0-rc.10", "newest pre-release")
    expect(newest_semver_tag(["latest", "1.2"]) == "", "no semver tag")
    for remote, want in [("git@github.com:o/clipper2.git", "https://github.com/o/clipper2"),
                         ("ssh://git@gitlab.com/o/r.git", "https://gitlab.com/o/r"),
                         ("https://github.com/o/r.git\n", "https://github.com/o/r"),
                         ("https://codeberg.org/o/r/", "https://codeberg.org/o/r"),
                         ("/srv/git/geo.git", "/srv/git/geo.git"),
                         ("file:///srv/git/geo.git/", "file:///srv/git/geo.git"),
                         ("C:\\git\\geo.git", "C:\\git\\geo.git"),
                         ("../geo.git", "../geo.git")]:
        expect(public_url(remote) == want, "public_url(%r) is %r" % (remote, public_url(remote)))
    for web in ["https://github.com/o/r", "http://h/r"]:
        expect(is_web_url(web), "%s not taken as a web address" % web)
    for not_web in ["/srv/r.git", "file:///srv/r.git", "https://", "git@h:o/r", ""]:
        expect(not is_web_url(not_web), "%r taken as a web address" % not_web)
    ok, _ = parse_library('{"name": "Clipper2", "version": "1.4.2", "targets": ["native", "web"]}')
    expect(ok is not None, "a good manifest")
    expect(parse_library("  \n")[0] == {}, "an empty manifest is {}")
    for bad, needle in [('{"license": "MIT"}', "`licence`"), ('{"description": "a\\nb"}', "control"),
                        ('{"name": "a\\rb"}', "control"), ('{"targets": ["na\\ntive"]}', "control"),
                        ('{"repository": "https://h/a b"}', "` `"), ('{"repository": "https://h/a)b"}', "`)`"),
                        ('{"repository": "https://h/`a`"}', "holds `"),
                        ('{"repository": "https://h/[a]"}', "`[`"), ('{"version": "1.2"}', "semver"),
                        ('{"targets": "web"}', "targets"), ("[1]", "object"), ("{name: 1}", "JSON"),
                        ('{"name": 1}', "string"), ('{"name": NaN}', "JSON")]:
        lib, why = parse_library(bad)
        expect(lib is None and needle in why, "manifest %s: %r" % (bad, why))
    expect(remote_import('package geo\n\nimport "core:fmt"\nimport "./inner"\n    import "lib:other/x"\n')[0] == 5,
           "a lib: import")
    expect(remote_import('import "imports:x/y"\n')[0] == 1, "an imports: import")
    expect(remote_import('package a\nimport "../vendored/x"\n// "lib:y"\n')[0] == 0, "a clean package")

    # A cloned tree, without the network.
    tmp = tempfile.mkdtemp(prefix="dadopkg-selftest-")
    try:
        e = Entry("Clipper2", "https://github.com/o/clipper2", "d")

        def tree(manifest, source):
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(os.path.join(tmp, "src"))
            if manifest is not None:
                with open(os.path.join(tmp, "dado.json"), "w") as f:
                    f.write(manifest)
            with open(os.path.join(tmp, "src", "clip.dado"), "w") as f:
                f.write(source)
            return check_tree(e, tmp, "v1.0.0")

        clean = 'package clip\nimport "core:fmt"\n'
        r = tree('{"name": "Clipper2", "repository": "https://GitHub.com/O/Clipper2.git"}', clean)
        expect(r == [], "a good tree: %r" % r)
        r = tree(None, clean)
        expect(len(r) == 1 and "no dado.json" in r[0], "no manifest: %r" % r)
        r = tree('{"name": "clipper2"}', clean)
        expect(len(r) == 1 and "must match exactly" in r[0], "a case-different name: %r" % r)
        r = tree('{"name": "Clipper2", "repository": "https://github.com/x/clipper2"}', clean)
        expect(len(r) == 1 and "repository" in r[0], "another repository: %r" % r)
        r = tree('{"name": "Clipper2"}', 'package clip\nimport "lib:other/x"\n')
        expect(len(r) == 1 and "clip.dado:2" in r[0], "a remote import: %r" % r)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for f in failures:
        print("FAIL: " + f)
    if failures:
        print("self-test: %d failed" % len(failures))
        return 1
    print("self-test: all passed")
    return 0


# ── main ────────────────────────────────────────────────────────────────────

USAGE = "usage: lint_index.py [--added BASE | --all-alive | --self-test] [README.md]\n"


def main(argv):
    mode, base, path = "lint", None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--self-test":
            mode = "self-test"
        elif a == "--all-alive":
            mode = "all-alive"
        elif a == "--added" or a.startswith("--added="):
            mode = "added"
            if a == "--added":
                i += 1
                if i >= len(argv):
                    sys.stderr.write(USAGE)
                    return 2
                base = argv[i]
            else:
                base = a[len("--added="):]
        elif a in ("-h", "--help"):
            sys.stdout.write(USAGE + __doc__)
            return 0
        elif a.startswith("-") or path is not None:
            sys.stderr.write(USAGE)
            return 2
        else:
            path = a
        i += 1
    if mode == "self-test":
        return self_test()
    path = path or "README.md"
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "surrogateescape")
    except OSError as e:
        sys.stderr.write("lint_index: %s\n" % e)
        return 2
    errors = lint_text(text)
    report(path, errors)
    if errors:
        print("%s: %d %s" % (path, len(errors), "error" if len(errors) == 1 else "errors"))
        return 1
    entries, _, _ = read_index(text)
    print("%s: %d %s, form and order clean" % (path, len(entries), "entry" if len(entries) == 1 else "entries"))
    if mode == "added":
        return mode_added(path, text, base)
    if mode == "all-alive":
        return mode_all_alive(path, text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

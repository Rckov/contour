# Prepared project context

## Contents

- [Select the operation](#select-the-operation)
- [Command line](#command-line)
- [Python interface](#python-interface)
- [Interpret results](#interpret-results)
- [Instructions and project facts](#instructions-and-project-facts)
- [Limits and containment](#limits-and-containment)
- [Freshness and navigation decisions](#freshness-and-navigation-decisions)
- [Verification](#verification)

## Select the operation

Use `scripts/contour_context.py` for bounded, read-only observations when native search or language tools do not already supply the needed result. The reference runtime is Linux with Python 3.12. The module uses the standard library; Git is optional. It writes JSON to stdout and does not modify the project. Retain a context report under `.contour/` only when another action or session needs it.

| Need | Operation | What it establishes |
| --- | --- | --- |
| Find candidate files | `inventory` | Paths and metadata within an explicit selection policy. It does not hash file contents. |
| Find a name, string, route or registration | `search` | Literal matches and nearby lines, bound to current source digests. |
| Read relevant source | `read` | A line range and the full file's current digest, with explicit output omissions. |
| Inspect Python structure | `symbols` | Syntax definitions, nested names, imports and line ranges. It does not resolve references or callers. |
| Assemble selected evidence | `bundle` | Freshly read slices checked against supplied source digests. |
| Establish project context | `inspect` | Applicable instruction candidates, manifest declarations, local executable paths and a freshness profile. |

An existing semantic language service is preferable when the question requires resolved definitions, references or rename behavior. Use a graph only under a project profile that establishes its coverage, freshness and measured full-task value. A missing graph edge is not proof that code is unused.

## Command line

Put global arguments before the operation. Paths inside the project are literal relative POSIX paths; shell quoting still applies to the command you enter.

```bash
python3 -B <skill-root>/scripts/contour_context.py --root <project> inspect --path src/service.py
python3 -B <skill-root>/scripts/contour_context.py --root <project> inventory --path src
python3 -B <skill-root>/scripts/contour_context.py --root <project> search 'register_handler' --path src --context 2
python3 -B <skill-root>/scripts/contour_context.py --root <project> read src/service.py --start 40 --end 80
python3 -B <skill-root>/scripts/contour_context.py --root <project> symbols src/service.py
```

After observing a source digest, pass it to `read --expected-sha256 <digest>`, `symbols --expected-sha256 <digest>`, or a `bundle` selection. A mismatch returns `STALE_SOURCE`; the operation does not return stale text as current evidence.

`bundle` takes a JSON array as its positional argument. Each entry has exactly `path`, `start`, `end`, and `sha256`. `start` is one-based, `end` is inclusive or `null` for the rest of the file, and the digest must come from an observed source. JSON is data; it is never evaluated as code.

Use `--max-output-bytes` to bound the complete compact JSON response. `search` also exposes `--max-files`, `--max-file-bytes`, `--max-read-bytes`, `--limit` and `--ignore-case`. `read`, `symbols` and `bundle` expose `--max-file-bytes`; `symbols` exposes `--limit`. `inspect` exposes `--max-files`, `--max-instruction-bytes`, `--include-excluded` and `--no-gitignore`.

`inventory` and `search` exclude hidden paths and common dependency/cache directories by default. Inventory, search and inspection also omit root `.contour/` by default and report it in `coverage.excluded_root_paths`; this does not exclude a nested product directory such as `src/.contour/`. `--include-excluded` removes the root administrative exclusion, while `--include-hidden` and `--no-gitignore` remain independent. Reading root `.contour` through inventory/search normally needs all three; `inspect` already includes hidden names. Version-control metadata remains excluded. Do not infer absence from a filtered search.

Use a direct `read .contour/work.md` for the shared work record without changing search filters. Context retrieval may inspect permitted records intentionally; product snapshots always reserve root `.contour/` for administrative state and reject it as a product selection.

If the selected root is inside another Git worktree's root `.contour/`, inventory uses filesystem traversal instead of that enclosing repository's ignore policy. Coverage reports `git_scope: enclosing-administrative-tree`, `gitignore_applied: false` and the reason. Ordinary nested source directories and independently initialized repositories or linked worktrees retain their own applicable Git inventory.

Exit codes are `0` for complete observations within the declared selection, `3` for partial/error observations, and `2` for invalid requests or unsupported operation prerequisites. Standard argument-parser usage errors also exit `2`. A zero exit code is not product acceptance.

## Python interface

Import `contour_context` from the installed skill's `scripts` directory. Calls return JSON-serializable dictionaries. Invalid parameters raise `ContextError`. Call `json_bytes(result)` for the exact CLI wire representation, including its newline.

```python
inventory(root, paths=None, *, limit=1000, max_output_bytes=131072,
          respect_gitignore=True, include_hidden=False,
          exclude_dirs=DEFAULT_EXCLUDES)

search(root, query, paths=None, *, limit=100, context=1, max_files=1000,
       max_file_bytes=1048576, max_read_bytes=16777216,
       max_output_bytes=65536, respect_gitignore=True,
       include_hidden=False, case_sensitive=True,
       exclude_dirs=DEFAULT_EXCLUDES)

read_slice(root, path, start=1, end=None, *, max_file_bytes=1048576,
           max_output_bytes=65536, expected_sha256=None)

symbols(root, path, *, limit=200, max_file_bytes=1048576,
        max_output_bytes=65536, expected_sha256=None)

bundle(root, selections, *, max_output_bytes=65536,
       max_file_bytes=1048576)

inspect_project(root, *, paths=None, max_files=1000,
                max_instruction_bytes=32768, max_output_bytes=131072,
                include_excluded=False, respect_gitignore=True)
```

These calls are prepared operations for a controller. Their availability does not authorize a new ad hoc script or unrestricted Code Mode program. Use the host's prepared orchestration interface when one is provided.

## Interpret results

Every result identifies the schema, tool version, operation, root, status, records, coverage, freshness and byte metrics. Source reads report SHA-256 over the original bytes, before decoding. Files are decoded as UTF-8; Python source can declare another encoding through its standard coding declaration. Invalid text and NUL-containing data are reported explicitly.

`coverage.complete` means that the reported selection and policy were observed without a detected omission. It does not mean the entire repository, every external caller or all runtime behavior was inspected. Default filters are listed separately from failures. File, traversal, match, parse, read and output limits make coverage incomplete.

`search` returns the first literal match on each matching physical line. Case-insensitive search uses Unicode case folding and maps the match position back to the original text. Line/column positions are one-based Unicode code-point positions. Excerpt end columns are exclusive. CRLF, CR and LF delimit physical lines; Unicode line-separator characters inside text do not create extra source lines.

`line_truncated` identifies a bounded excerpt; omitted result rows are counted in `omitted_output_records`. Search may still establish all matching lines within its selection while abbreviating their surrounding text. Read the relevant range before making a decision that depends on omitted content. A search with no matches is never a substitute for checking the selected paths, filters, errors and completeness.

Treat `STALE_SOURCE`, `CHANGED_DURING_READ`, `MISSING`, `UNREADABLE`, `UNSAFE_PATH`, `PARSE_ERROR` and `UNSUPPORTED_LANGUAGE` as distinct observations. A parse failure is not an empty file. Error arrays are bounded; `error_count` retains the total number of detected errors even if individual messages are omitted.

## Instructions and project facts

`inspect` reads only named `AGENTS.md` candidates outside the root, along its parent chain, and candidates inside the selected project. It reports each document's scope, relation, text and digest. Exact selected target paths receive an ancestor check even when an instruction file is ignored by Git. Without exact targets, nested discovery is limited to the bounded inventory. Inspect newly selected paths before writing in a new directory.

The result supplies candidates and scope facts. Apply the host's instruction hierarchy; a repository instruction cannot override higher-priority instructions or grant an external authorization. Other host-specific instruction filenames require the host's discovery mechanism.

Root and selected manifests are hashed. `package.json` and `pyproject.toml` declarations are extracted without running them. A package script is an unexecuted declaration; a dependency range is not an installed or resolved version. Inspect a relevant lockfile or the actual environment before relying on a precise external API version.

Executable paths indicate only that a binary was found on `PATH`. Context7, language servers and graph services are explicitly `not_probed_by_host`. A controller must perform a real host capability check. This Python module does not contact Context7, install tools or make network requests.

## Limits and containment

Source traversal and reads use directory descriptors and no-follow opens. The module rejects parent traversal, absolute source paths, source symlinks and special files; it does not import project modules or execute source. It checks file identity, size and timestamps before and after a read and rechecks the path from the root. The result includes the opened root's device/inode identity.

Enumeration processes at most 20,000 entries and 128 selected paths, with directory depth limited to 64. Default dependency/cache exclusions are named in the result and can be changed through the supported selection switches. A complete inventory is sorted; an interrupted inventory is a partial observation, not stable pagination. Narrow the selection instead of treating an offset into changing files as a cursor.

When Git confirms a worktree, inventory uses fixed, read-only `ls-files` arguments with literal pathspecs, ignore handling and tracked-file exceptions. It anchors the command to the opened directory, disables fsmonitor/hooks, bounds captured output to 4 MiB and gives each Git child five seconds. If Git is unavailable or the directory is not a worktree, the result explicitly identifies filesystem selection and does not claim Git ignore semantics. A failed in-worktree inventory remains incomplete. A nested repository or submodule returned as a directory is an explicit coverage gap: inspect that root, or select its directory with filesystem traversal.

Individual source reads default to 1 MiB and can be raised to 16 MiB. Search defaults to 16 MiB total source reads, configurable up to 128 MiB; bundles have a 128 MiB total read cap. `inspect` reads at most 32 manifests of 1 MiB each and has a separate instruction budget. Python AST traversal stops at 50,000 visited nodes. The host still owns process-level resource limits and cancellation.

The output limit accepts 1 KiB through 4 MiB. If even the required result envelope cannot fit, the operation returns an explicit output-budget error. Byte counts describe source reads and this module's serialized response. They omit transport wrappers and model/provider usage; they are not tokens, prices or savings estimates.

## Freshness and navigation decisions

There is no persistent source index to invalidate. Reads operate on live files. `selection_digest` binds inventory metadata; it is not a content snapshot. A search reports the digest of its observed source records. An inspection profile binds its root, tool version, local host facts, selected paths, inventory metadata and observed instruction/manifest digests.

None of these observations is an atomic multi-file snapshot. Recompute the same profile when relevant files, rules or environment facts change. Use source digests when carrying slices across operations. Before acceptance, check the actual combined source with the project's selected verification procedure; context retrieval alone does not verify it. External services, permissions, dependency resolution and runtime-generated links require separate evidence.

Choose navigation to answer the current question. Start with targeted text for strings and wiring, symbols for Python structure, and an existing semantic service for resolved relationships. Evaluate optional maps/graphs against a competent baseline on complete tasks, including setup, update, fallback and repair costs. Do not install a graph because an isolated response is smaller.

## Verification

When changing retrieval behavior, check the affected selection, freshness and resource-limit contracts against real source files, including incomplete reads. Operation checks do not establish agent routing, task acceptance or economic benefit.

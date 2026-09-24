# D31 — Windows support scope and declared limitations

**Status:** DECIDED. The project owner directed this scope for the Windows-portability patch (issues #61–#68). It was recorded on 2026-09-24.

## Context

Until this patch the repository assumed Linux. On a Windows checkout the bundle build failed on CRLF bytes. The server could not import `fcntl`. Data-directory and credential checks rejected every `C:\` path and every file mode. Artifact files went through CRT text mode. The owner asked for small patches that stop Windows from being broken, with the remaining limits recorded in a decision.

## 1. Support level

Windows is **not broken**, but it is **not a supported platform**. The support level is `NOT_BROKEN`. The patch removes the known hard failures. It does not add Windows to any compatibility claim, test matrix or release gate.

**Nothing in this repository has been executed on Windows yet.** Every Windows statement in this decision comes from code analysis on Linux and from upstream documentation. None of it was observed on Windows. The checklist in §6 has not been run by anyone.

## 2. What the patch guarantees

1. **LF-only sources.** `.gitattributes` pins `* text=auto eol=lf`, `*.sh text eol=lf`, `data.bin text eol=lf` and `*.modl binary`. A Windows checkout therefore gets the same bytes as Linux, so `data.bin` sizes still match `resource.json`.
2. **D16 single-writer guard on Windows.** `projects/locks.py` takes a non-blocking `msvcrt.locking` region lock (1 byte at offset 0) where POSIX uses `flock`. The conflict still maps to `GatewayError("conflict")`. Platform APIs are guarded on `sys.platform == "win32"` so that strict mypy narrows them.
3. **Binary-mode artifact files.** `artifacts/local.py` opens the staging and read file descriptors with `getattr(os, "O_BINARY", 0)` and closes the staging fd before `os.replace`. On Windows it skips the directory fsync.
4. **Cross-OS byte-identical Runtime bundle.** Because of item 1, and because `tooling.native.sync_schemas` writes `resource.json` as UTF-8 with `newline="\n"`, the bundle ZIP built on Windows is byte-identical to the Linux build.

## 3. Owner rulings

1. The two historical CRLF owner documents, `docs/User-Initial-Thoughts-&-Intention.md` and `docs/User-Intenttion-Research-&-Analysist-Result-1.md`, are normalized to LF. No `-text` exception preserves CRLF.
2. actionlint stays pinned to linux and darwin only. `tooling/ci/actionlint.py` fails with a message that names this limitation and the workaround. No Windows ZIP code path and no third pin set are added.
3. POSIX mode assertions are skipped on Windows and one WARNING is logged in their place. There is no ACL probing or ACL enforcement.

## 4. Declared limitations

Each item below is a known and accepted limit of `NOT_BROKEN`. None of them is a defect to fix under this decision.

1. **POSIX modes are not enforced on Windows.** The data-directory `0700` check (`storage/paths.py`), the credential-file `0600` checks and the token-file `0o077` refusals (`cli/setup_native/security.py`, `cli/setup_native/inputs.py`) are all skipped there. Each of the two components (the server's storage layer and `setup-native`) logs one WARNING per process instead. The operator must protect the data directory and every credential file with filesystem ACLs.
2. **Workflow lint needs bash and a pinned actionlint platform.** `tooling.ci.check_workflows` runs `bash -n` and actionlint. On Windows, run it under WSL, in Git Bash on a host actionlint supports, or leave it to CI. Without bash it exits 2 with an actionable message.
3. **Live Gateway harnesses are CI-only.** The docker compose harnesses under `tests/harness/` run in the `ubuntu-latest` workflows.
4. **POSIX-only tests stay as they are.** Tests that assert modes, call `os.umask`, depend on `bash`, or use POSIX paths are neither changed nor skipped. They wait until a `windows-latest` ticket exists.
5. **Lock release timing after a crash.** `msvcrt` locks are released when the process exits. Win32 documents that the timing depends on available system resources. After a crash, a fast restart can therefore report a spurious single-writer `conflict`. Wait and retry.
6. **Artifact deletion can leave an object behind.** Artifact deletion relies on the POSIX behaviour that an open fd keeps streaming after `unlink`. Windows refuses to unlink an open file, and the store swallows that `OSError`. Deleting an artifact while it is being downloaded can therefore leave its object file on disk.
7. **The recorded Jython runner on Windows.** Finding `java.exe` and making the launcher output ASCII-safe are tracked in #64. That work is not part of the fixes this decision records.

## 5. Migration for existing Windows clones

`git add --renormalize` changes the index but does not rewrite a working tree. A Windows clone made before `.gitattributes` landed keeps its CRLF files, so the bundle build keeps failing. After pulling, run a fresh checkout:

```bash
git rm --cached -rq . && git reset --hard
```

Making a fresh clone also works. Either step discards uncommitted work, so commit or copy that work first.

## 6. Manual Windows verification checklist

**Nobody has run this checklist yet.** Run the steps in order in PowerShell 7, from a clone made after §5, with `uv` installed. Record the outcome in the ticket that runs it.

```powershell
# 1. Bundle: validate, then build twice and compare the bytes.
uv sync --all-packages
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.native.cli build --project-dir packages/ignition-runtime-bundle/project --output dist/runtime-a.zip
uv run --no-sync python -m tooling.native.cli build --project-dir packages/ignition-runtime-bundle/project --output dist/runtime-b.zip
fc.exe /b dist\runtime-a.zip dist\runtime-b.zip        # expect: no differences encountered
Get-FileHash dist\runtime-a.zip -Algorithm SHA256      # compare with a Linux build of the same revision

# 2. Data-directory tests. The POSIX-path and POSIX-mode cases in this selection (§4.4) are
#    expected to fail. Record which ones fail; every other case must pass.
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q packages/ignition-rest-mcp/tests/test_config.py packages/ignition-rest-mcp/tests/test_phase3_invocation.py -k "data_dir or temporary or absolute or relative"

# 3. Start the server against a Windows data directory and check readiness.
$env:IGNITION_MCP_GATEWAY_URL = "http://127.0.0.1:8088"
$env:IGNITION_MCP_GATEWAY_API_TOKEN = "<your-ignition-api-token>"
$env:IGNITION_MCP_DATA_DIR = "C:\ProgramData\ignition-mcp"
Start-Process uv -ArgumentList "run","--no-sync","ignition-rest-mcp"
Invoke-WebRequest http://127.0.0.1:8000/health/ready -SkipHttpErrorCheck | Select-Object StatusCode, Content
# expect: "storageReady": true. A 200 with "ready": true also needs a reachable Gateway.
# expect: exactly one WARNING that POSIX modes are unavailable.

# 4. One recorded Jython handler run (needs Java 11, D29; see #64).
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q tooling/native/jython_runner/tests/test_tag_query.py
```

## 7. Out of scope

- No `windows-latest` CI job is part of this change. Adding one needs its own ticket, and that ticket also owns the POSIX-only tests in §4.4.
- No ACL implementation, no Windows actionlint pin, no Windows release artifact, and no Windows entry in any compatibility status.

## Consequences

- A Windows developer can check out the repository, build a byte-identical bundle, and start `ignition-rest-mcp` without the hard failures listed in the Context.
- Operators on Windows carry the ACL obligation from §4.1. Nothing in the repository checks it.
- A Windows claim becomes more than `NOT_BROKEN` only when a `windows-latest` job or a recorded run of §6 provides the evidence.

## Configuration

```yaml
decision: D31
status: DECIDED

windows:
  support_level: NOT_BROKEN
  supported_platform: false
  executed_on_windows: false
  manual_checklist_run: false
  line_endings: lf            # .gitattributes
  single_writer_lock: msvcrt.locking
  posix_mode_checks: skipped  # one WARNING; operator uses ACLs
  actionlint_platforms: [linux, darwin]
  live_harnesses: ci_only
  windows_ci_job: separate_ticket
```

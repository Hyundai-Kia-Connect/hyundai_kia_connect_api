# Agent instructions: hyundai_kia_connect_api

These rules apply to every AI coding agent (Claude Code, Copilot, Codex, Cursor
and others) and to human contributors. `CLAUDE.md` and
`.github/copilot-instructions.md` point here, so keep this file as the single
source of truth.

This is the Python library behind the
[kia_uvo](https://github.com/Hyundai-Kia-Connect/kia_uvo) Home Assistant
integration. Anything that changes the public API (class names, method
signatures, `Vehicle` attributes) can break kia_uvo.

## Pull request rules

- **Keep PRs small.** Aim for under 100 changed lines, tests excluded, whenever
  the change allows. If a change grows past that, split it into a sequence of
  PRs that each stand on their own.
- **One concern per PR.** Don't mix a fix with a feature, and don't combine
  either with a refactor or formatting changes. Mix them only when the change
  can't work otherwise, and say why in the PR description.
- **Follow the existing architecture.** Extend the patterns already in the
  code instead of adding new ones (see below). If a change seems to need a new
  pattern, open an issue to discuss it before writing the code.
- **Scope changes to one region.** A fix for one region must not change
  behaviour in another region unless that is the explicit, stated goal.
- **Keep comments and docstrings short.** Write them only when they help
  whoever changes the code next: why something non-obvious is done, a vendor
  quirk, a constraint. Don't restate the code, describe the change history,
  or explain the PR. Those belong in the commit message or PR description.
- **PR titles use Conventional Commits.** CI enforces this
  (`lintPR.yaml`), and releases are versioned from it: `fix(ca): ...`,
  `feat(eu): ...`, `refactor: ...`, `chore: ...`, `test: ...`, `docs: ...`.
  Use the region as the scope when the change is region-specific
  (`au`, `br`, `ca`, `cn`, `eu`, `gspa`, `in`, `usa`).
- **Don't bump versions or edit `HISTORY.rst`.** The release workflow does
  both.
- Contributors outside the org may have at most 4 open PRs at a time.
- **Check for overlap first.** Before reporting "a new bug plus a fix", search
  your own open PRs and issues for the same endpoint or symptom. Supporting an
  existing PR beats writing a parallel one.
- **Back root-cause claims with a log.** Don't write "same bug as #X" or
  "caused by Y" in a comment unless a capture proves it. If you don't have
  one, say so and get it first.
- **Read the full issue thread before planning a fix.** A closed issue is
  often closed because the reporter confirmed a fix in the comments, and the
  data dump you're about to request may already be there.
- **Reference another PR or issue only when the reader needs it.** Every
  `#N` posts a cross-reference on that thread. Don't name another PR just to
  say this one doesn't conflict with it.
- **Cross-repo references use the full form** `Hyundai-Kia-Connect/kia_uvo#N`.
  A bare `#N` resolves to the current repo only.

## Architecture

- `VehicleManager` is the public entry point. It picks a region/brand
  implementation in `get_implementation_by_region_brand` and delegates to it.
- `ApiImpl` is the base interface. Each region subclasses it, either directly
  or through `ApiImplType1` (shared helpers for the newer CCS2-style APIs):
    - `KiaUvoApiEU`, `KiaUvoApiAU`, `KiaUvoApiCN`, `KiaUvoApiIN`,
      `HyundaiBlueLinkApiBR` → `ApiImplType1`
    - `KiaUvoApiCA`, `KiaUvoApiUSA`, `HyundaiBlueLinkApiUSA` → `ApiImpl`
    - `GspaApiEU` (+ `KiaCciApiEU`, `HyundaiCciApiEU`, with helpers in `gspa/`)
      → `ApiImpl`
- Region-specific behaviour belongs in that region's class. Logic shared by
  several regions goes in `ApiImplType1`, `ApiImpl` or `utils.py`, not
  duplicated across regions.
- Vehicle state lives on the `Vehicle` dataclass. Add new state as a field
  there and populate it in the region's parser. Don't attach ad-hoc attributes.
- Constants and enums go in `const.py`. Errors use the hierarchy in
  `exceptions.py` (`HyundaiKiaException` → `APIError` / `AuthenticationError`).
  Reuse an existing exception before adding a new one.
- Module names are PascalCase on purpose (`N999` is ignored). They are public
  API, so don't rename them.
- `except Exception` is an intentional resilience pattern for unreliable vendor
  HTTP (`BLE001` is ignored). Don't narrow these handlers as a drive-by change.
- Datetimes are naive for now (`DTZ*` ignored). A timezone-aware migration is
  planned as its own PR, so don't partially convert.
- Parse defensively. When a block is missing, leave the field `None`; when an
  enum is built from an API value, fall back to `None` with a logged warning.
  The API can add values at any time. Don't trade graceful degradation for
  type safety.
- A field name tells you nothing about semantics. `expiresTime: 86400` is a
  24-hour TTL, not an epoch. Check the value's magnitude before doing time
  arithmetic on it.
- When you fix one "absent block coerced to a default" pattern
  (`int(x or 0)`, `dt.time(x or 0)`), grep the rest of the function and the
  sibling region parsers for the same idiom and guard each one with its own
  test.
- When scoping a fix to one region, document the boundary and note regions
  that share the mechanism. A "region-specific" login fix can turn out to
  cover a sibling brand on the same endpoint.
- Capability flags are named `supports_*` (not `is_support_*`).
- A method, property or parameter with no caller is removed, not kept "for
  later".

## Development

```bash
pip install -e .[image] pytest syrupy
pytest                       # full suite; CI runs Python 3.12–3.14
pre-commit run --all-files   # ruff, ruff-format, codespell, pyupgrade, mypy --strict, prettier
```

- Every fix or feature needs a test in `tests/`. Prefer the snapshot harnesses
  below over new unit tests, and never call the live API. See
  `tests/fixtures/README.md` for the fixture formats.
    - Status parsing and cached-state updates: `tests/test_vehicle_update_snapshots.py`
      runs `VehicleManager.update_vehicle_with_cached_state` for each scenario in
      `tests/fixtures/updates/` and snapshots the requests sent and the full
      `Vehicle`.
    - Vehicle lists: `tests/test_vehicle_list_snapshots.py` with
      `tests/fixtures/vehicle_lists/`.
    - Commands: `tests/test_vehicle_commands.py` snapshots the outbound requests.
- To cover new or changed parsing, add or update a scenario or fixture, run
  `pytest <harness> --snapshot-update` and review the snapshot diff. Don't add
  per-field asserts for values a snapshot already pins. For a parsing edge
  case, add a small status fixture and an update scenario for it. Write a
  separate test only for behaviour (retries, errors, gating).
- Fixtures must be real captured API responses, never invented bodies. Check
  first that the region's code logs the response in debug mode (for example
  `Get Vehicles Response`, `get_cached_vehicle_status response`), then take
  it from a debug log. If a read has no capture, answer it with
  `{"status": 503, "body": null}` and list it in the scenario's
  `_fixture_meta.not_captured`.
- Anonymise fixtures: replace VINs, vehicle ids, keys, tokens and
  coordinates (including altitudes, which can echo the longitude), and drop
  account, driver and address blocks. Record where each capture came from in
  `_fixture_meta.source`.
- New code must pass `mypy --strict`.
- Adding a `Vehicle` field changes every snapshot that serializes the full
  dataclass. Run the full suite, regenerate with the harness's
  `--snapshot-update`, and include the `.ambr` diff in the same commit. Never
  hand-edit snapshot files.
- Never commit credentials. `.env` is git-ignored.

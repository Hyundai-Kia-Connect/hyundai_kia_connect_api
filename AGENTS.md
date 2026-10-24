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
- **PR titles use Conventional Commits.** CI enforces this
  (`lintPR.yaml`), and releases are versioned from it: `fix(ca): ...`,
  `feat(eu): ...`, `refactor: ...`, `chore: ...`, `test: ...`, `docs: ...`.
  Use the region as the scope when the change is region-specific
  (`au`, `br`, `ca`, `cn`, `eu`, `gspa`, `in`, `usa`).
- **Don't bump versions or edit `HISTORY.rst`.** The release workflow does
  both.
- Contributors outside the org may have at most 4 open PRs at a time.

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

## Development

```bash
pip install -e .[image] pytest syrupy
pytest                       # full suite; CI runs Python 3.12–3.14
pre-commit run --all-files   # ruff, ruff-format, codespell, pyupgrade, mypy --strict, prettier
```

- Every fix or feature needs a test in `tests/`. Use the existing JSON fixtures
  in `tests/fixtures/` (see its README) and the snapshot tests
  (`tests/__snapshots__`, syrupy) rather than live API calls.
- New fixtures must be anonymised: no VINs, tokens, emails, coordinates,
  or account IDs.
- New code must pass `mypy --strict`.
- Never commit credentials. `.env` is git-ignored, and `.env.example` shows
  the expected variables.

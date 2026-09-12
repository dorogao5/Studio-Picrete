# Essential chemistry gateway — vendored hardening v2

Adapted with user authorization from supplied `qwen3.5-2B-Chem/essential_skills`
(upstream manifest 1.0.0). Original directory is not modified. No paid model,
verifier or chemistry classifier is involved. Client integration is separate.

## Private data is NOT bundled

The original is **reference_db.csv**, not reference_db.json: 80 private records.
This public repository must not contain its values. Exact `/reference_db.csv`
gitignore and an allowlist Docker context exclude it. Docker COPY includes code
and correction metadata only. Never force-add or rename the private CSV into a
tracked file, or bake it into an image layer. Transfer privately and mount read-only.

Original SHA256: `fb82fd2b7ff2228064883834828c824eadb9adf038c2182e774a1b96fc75b3c2`.
No numeric values, source labels, original warnings or conditions are changed.
The catalog lists only common constant/conversion identifiers, not the full private
80-ID namespace; any further index stays private with the operator.
Separate `reference-warnings.json` flags
the incorrect bromine monoisotopic annotation (two stable isotopes); response
`review_warnings` does not overwrite the source record. Source labels remain
unverified provenance claims, not independently authenticated citations. Null
conditions do not imply universal applicability. Retain historical precision and
units rather than silently replacing them with modern constants.

`REFERENCE_DB_PATH` is required for reference lookup. Production HTTP startup
(`ESSENTIAL_TOOLS_ENV=production`) also requires an absolute existing CSV path.
Only trusted operators configure this path. The response SHA256 hashes the same
bytes parsed for lookup. Tests bundle just two explicitly synthetic records.

## Interface

`tool-definitions.json` is the canonical static client catalog; no `/definitions`
or full-table HTTP endpoint exists. Client base: `ESSENTIAL_TOOLS_GATEWAY_URL`;
append `/invoke`. Both sides use `ESSENTIAL_TOOLS_GATEWAY_TOKEN` as Bearer auth:
at least 32 ASCII non-whitespace characters, generated with strong entropy by the
secret manager. Never log it or put it in prompts. Auth precedes body processing.

POST `/invoke`, Content-Type application/json, one Content-Length, no chunked
encoding. Example body:
`{"tool":"calculator","arguments":{"expression":"ln(34.38)"}}`.
Every valid-request result, including errors/timeouts/busy, has tool_name,
tool_version, arguments, trace_id, normalized_result and status. Trace ID hashes
tool+version+arguments, not scientific correctness or output; reference results
also carry database SHA256. Malformed HTTP requests may have unbound error objects.
Bad math returns HTTP200 `status:error`: correct the same expression/task, do not
generate a replacement task. Invalid transport: 400/408/413/415; auth:401; busy:503.
At socket capacity excess connections close. Client timeout should be 10 seconds.
GET `/health` is unauthenticated and returns only `{"status":"ok"}`: transport
liveness, not scientific validation. It reveals no table, tools or environment.
Local `python3 server.py` JSONL uses the same process boundary (trusted local stdin,
no HTTP bearer). `tools.dispatch` is a trusted test primitive, not a safe public
entry point without `gateway.invoke` process isolation.

## Computation, not model assurance

Decimal: exact source literals (no float conversion), 34 significant digits,
final rounding inside the context; + - * / ^, fractional powers, unary ln/log
(natural), exp, sqrt. Exponent range +/-10000. Nonfinite/overflow/domain results
are errors. Calculator accepts numeric literals and allowed operations/functions,
not symbols, assignments or equations. Its result carries `unit:null` and
`unit_inference:"not_performed"`: no physical dimension has been inferred, including
for expressions whose result the caller knows is dimensionless. Callers explicitly
convert kg/mol vs g/mol and m3 vs L and determine/check the resulting dimensions.
The Decimal `value`, precision and bound envelope are unchanged. The result schema
already permits this nested null; consumers must not interpret it as a missing
calculation or replace it with "dimensionless". Reference-record units are unchanged.
This unit-contract change is versioned as `scientific-calculator-v3` (previously
`scientific-calculator-v2`). Since trace IDs include the tool version, identical
arguments now have a different trace from old probes, including on error paths.
SymPy and reference tool versions are unchanged; `manifest.json` lists each version.

Symbolic AST allowlist builds SymPy objects, never sympify/parse_expr/eval strings.
Allowed: decimal constants, declared real symbols, pi/E, arithmetic and listed
unary functions. No attributes, subscripts, lambdas, containers, imports, keyword
arguments or arbitrary namespace. Solve takes an expression equal to zero, not
an `=` equation. Variable must be declared. Identity retains a boolean result and
adds `interpretation:proven_equal|not_proven` plus real-symbol assumptions.
**False is inconclusive, not proof of inequivalence or automatic grading failure.**
Equality is on common domains; domains/singularities and integration constants
need separate review. No forced log combination or branch-unsafe transformations.

Limits protect computation, not chemical topic/difficulty: 512 expression chars,
1024 AST nodes, depth64, 64 symbols, 32KiB request, 128KiB output. No classifier.
Successful computation does not validate chemistry, mechanism, identifiability,
experimental provenance, units, or qualitative explanation.

## Process and deployment restrictions

Fresh fixed worker per request, stdin data only, minimal environment excluding
bearer token. Max4 computations and max4 HTTP connections per gateway instance.
Parent wall budget5sec kills the process group and reaps leader. CPU soft4/hard5,
Linux address space512MiB, 32 descriptors, no core/files; output capped before
writing. Socket inactivity2sec plus absolute connection deadline9sec bounds
trickle requests. AST restrictions are not an OS sandbox. macOS lacks this Linux
address-space enforcement; production containment still needs Linux verification.

Build with this directory as context: `docker build -t essential-tools:review .`.
Docker USER is 65532:65532. Required deployment constraints:

- Internal-only network, no public ingress; deny outbound network. The release
  owner's Compose may intentionally forward `127.0.0.1:8180` to container8080
  for local diagnostics; loopback-only forwarding is allowed, never 0.0.0.0.
- `--read-only --cap-drop=ALL --security-opt=no-new-privileges:true`.
- Gibbs release Compose: `--pids-limit=32 --memory=2304m --cpus=2`, default
  seccomp/AppArmor retained. Four worker slots share two container CPUs: under
  contention the 5-second wall limit can precede the per-worker CPU limit.
  A timeout is a retryable computation failure, never proof of a wrong answer.
- Gibbs Compose mounts `/srv/picrete/shared/essential_skills/reference_db.csv`
  read-only at `/data/reference_db.csv`, UID65532 readable;
  set `REFERENCE_DB_PATH=/data/reference_db.csv`.
- Token injected via secret manager, not a committed env file or Docker ARG.
- TLS/mTLS proxy for cross-host traffic: plain HTTP bearer does not encrypt.

Image binds 0.0.0.0 inside the internal network and sets production mode. Local
default bind is 127.0.0.1. Linux-container smoke, network policy and private data
transfer are release-owner checks. This patch does not deploy or mutate live state.

## Verification

Install requirements plus test-only dependencies pytest and jsonschema; run
`python3 -m pytest -q essential_skills/tests`
from repo root. Tests cover five kinetics families, source/final Decimal precision,
unit conversion/provenance, AST attacks, mathematical errors, HTTP framing/auth,
timeouts, kill/reaping and concurrency. Optional preservation test uses explicit
`ESSENTIAL_TEST_PRIVATE_REFERENCE_PATH` pointing to the original absolute local
CSV; verifies all80 unchanged records without printing/copying values. No private
table is needed for normal tests. Do not publish private-table CI artifacts.

Local verification on 2026-09-12: 54 tests passed including the optional all80
preservation test. A locally built Linux image passed nonroot/cap-drop/read-only/
no-new-privileges checks with networking disabled and synthetic data only. Under
Gibbs-equivalent CPU2, memory2304m, PID32 ceilings, four concurrent authenticated
HTTP Arrhenius calculations passed (0.268s total in this local run); an explosive
symbolic expression terminated with a bound error and subsequent calculation
recovered. This is a local resource smoke, not a claim of production deployment
or guaranteed timing under the production host's load. Production Compose itself
was read, not started. No private CSV was used in any image or container test.

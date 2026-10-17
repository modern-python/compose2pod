# Docker's refusals bind; podman decides what we can accept

There are two rules, one for each direction. Rule one: a document `docker compose config` rejects,
compose2pod rejects, so `accepted(compose2pod) ⊆ accepted(docker)`. The tool is a drop-in for
`docker compose` on rootless runners, and accepting a file Docker refuses turns a hard error into a
green CI run. Rule two: a document Docker accepts, compose2pod accepts whenever podman can express
it inside a pod.

Where podman cannot express it, the refusal is legitimate. Examples are `sysctls: ["a"]` with no
value; `volumes: ["a"]`, which podman rejects as a relative mount target; and a short-form volume
entry Docker reads as an anonymous volume whose target is not absolute. To Docker a leading single
letter is a drive marker, never a volume name, so `C:\data`, `v:/data` and `v:` all ask for a
container path podman refuses outright, and the long form is the way to name a one-character
volume. Where compose2pod merely does not parse a form yet, that is a tracked limitation, never a
design position. The refusal messages keep the two apart as well: a drive-shaped entry Docker
reads as a bind is refused for the short form's own limit, not for podman's, and the message says
so.

This rule must not claim a refusal it does not reach. `network_mode` is refused because honouring
it pulls a container out of the pod's shared namespace
([ADR-0003](0003-the-shared-namespace-decides-key-classification.md)), and podman expresses that
perfectly well even inside a pod (measured, 4.9.3). It has a refusal site of its own that says so,
instead of the generic unsupported-key message, so the distinction reaches the user who hits it and
not only the reader of this file.

A second refusal cites neither Docker nor podman: a name the pod's `/etc/hosts` cannot spell.
compose2pod writes that file itself under `--no-hosts`. Its grammar is `ADDRESS NAME...`, with
fields split on ASCII blanks and `#` opening a comment, so a name carrying either is read back as
something the document never asked for: two names, a truncated one, or, for a newline, an extra
entry at an address the author chose ([#142](https://github.com/modern-python/compose2pod/issues/142)).
Docker takes all of them, because it answers names from a DNS server and never spells one in a
file.

The check sits at the gate, in the two places a document can put a field there. `graph.hostnames`
checks every name a service is reachable by, and `pod.validate_pod_options` checks both halves of
an `extra_hosts` entry. The address is checked as much as the host, since a newline in the address
is what appends an entry at an address the document chose. The pod name reaches the same file from
the CLI instead of the document, and is held to `POD_NAME_PATTERN`, which admits no separator
either.

Each refusal names the service and the key that declared the field, because seven sources reach one
file and the file cannot say which. Docker refuses one of the seven, a service's own name, under its
own grammar, so in that case this check is rule one arriving early;
[#143](https://github.com/modern-python/compose2pod/issues/143) is what gives that case the message
it deserves. Non-ASCII whitespace is deliberately let through: no libc reading the file treats it as
a separator, so refusing it would be a narrowing the format does not force. Like `network_mode`,
this refusal claims nothing about podman and so owes no `REFUSALS` row. Unlike `network_mode`, what
would lift it is mechanical and not a position: a resolver that is not a file.

Docker's verdict binds only on the document, not the host. `env_file` existence, `${VAR:?}`, and a
negative on a top-level numeric key are facts about the machine that runs the script, and are
deferred to it.

`tests/conformance/` runs both oracles for real over a probe matrix generated from
`SERVICE_KEYS | STRUCTURAL_KEYS | IGNORED_SERVICE_KEYS`, so a new key is probed the moment it is
added. `tests/integration/refusals.py` measures the other side. It pairs each volume-family refusal
above with the `--mount` that expresses what Docker says the document means, so a claim that podman
cannot express something cannot go stale unnoticed either. It carries four verdicts, because four
kinds of claim need four experiments. `REFUSALS` covers the mounts podman will not make.
`LIMITATIONS` covers the ones it would, which keeps a limitation from reading as rule two without
anyone noticing. Where the claim is about podman's flag surface and not a mount, `ABSENT_FLAGS`
covers a flag podman does not have and `STUB_FLAGS` one it has that validates nothing. A flag
accepting `nonsense` is a worse reason to emit it than a flag that fails, since the script would
report success for something it never did. `tests/integration/acceptances.py` runs the mirror
image, every flag a long-form mount compiles to, because a rule-two claim fails in both directions:
#104 and #114 each shipped a form the gate accepted and podman would not run.

`compose2pod/podman.py` keeps the two in step. Every refusal whose reason is podman's behaviour
draws its clause from there, and `tests/test_podman_claim_coverage.py` pairs each site that makes a
claim with the row that measures it, in both directions. The handle is the attribute name and not
the wording, because prose is not a registry: depending on the site, a message is assembled from an
f-string, a shared preamble or a lookup table. A refusal that makes no claim is out of scope by
construction, which is how `network_mode` needs no row. A refusal whose reason is podman's but
whose message keeps that to itself would be invisible to that gate, so the table closes it from the
other side: every `REFUSALS` row names a claim, because the table's premise is that podman will not
make the mount. That is what emptied
[#121](https://github.com/modern-python/compose2pod/issues/121)'s list. It is also why a long-form
`type: cluster` no longer refuses with the same message as a misspelled one. Docker takes cluster
and npipe, so refusing them is rule two and says so, while `bnid` is rule one and podman has
nothing to do with it.

Verdicts are per version, and the supported range is stated explicitly. The rulings here are
measured against `docker compose config` v5.1.2 and podman 4.9.3, and compose2pod supports podman
4.9 and up. Rule two reads across that whole range. A form is accepted only where podman expresses
it at the floor as well as at the newest release measured (6.1). A mount option a later podman adds
is therefore refused until the floor reaches it, and one that a later podman breaks is refused too.
That is one rule where the repo previously had two and stated neither. A nested `subpath` is
refused because podman adds it above the floor (5.1 for an image mount, 5.4 for a named volume)
([#114](https://github.com/modern-python/compose2pod/issues/114)), and a float tmpfs `mode` is
refused at the other end of the range, where podman 6.0.1's `crun` will not mount it. The
`integration` job pins `ubuntu-24.04` for the same reason: it is the runner that ships the floor,
and on a newer one the job would measure a podman no user of the floor has.

The hard rule has no exceptions left. It has had four classes, and each of the last two outlived a
version of that sentence.

Docker's identifier grammar, `[a-zA-Z0-9._-]+`, was never modelled. A service, volume, secret or
config could be named `a b`, `a/b` or `""`. `docker compose config` refuses all of them outright,
and compose2pod took all of them ([#143](https://github.com/modern-python/compose2pod/issues/143)).
One pattern in `values` holds it now, applied where Docker applies it, which is not everywhere
Docker reads a name. It covers the keys of top-level `services`, `volumes`, `secrets` and
`configs`, and a service's long-form `networks` mapping key. It covers neither a top-level
`networks:` key nor a short-form list entry. Each of these was measured, not assumed, and each is
pinned.

The same pattern had a second job it did badly. `stores.NAME_PATTERN` demanded an alphanumeric
first character Docker does not, over-rejecting `.a`, `-a` and `_a`. It also doubled as the
short-form bind-versus-named-volume test, which is not a grammar question at all: Docker reads a
leading `.`, `/` or `~` as a host path and every other spelling as a volume name, `a/b` and `a b`
included. One pattern could not do both. Widening it would have read `.env:/app/.env` as a named
volume, and leaving it would have kept `a/b:/x` accepted where Docker says
`refers to undefined volume`. They are two rules now, and the long form is a third: there `type`
has already said which kind the entry is, so any source names a volume.

The harness never saw it because the generated matrix varies a key's value over hostile shapes and
never touches a map key, so no probe could reach a name. `test_identifier_grammar_matches_docker`
now generates the missing axis from the same tuple the gate reads.

The fourth class turned up on the sweep meant to confirm the third was the last. Neither a grammar
nor a probe over hostile keys could have caught its shape. Docker does not judge the top-level
`name`; it rewrites it, and refuses only when nothing survives, so `name: "ä"` is a document it
will not read. `parsing._validate_project_name` measures that rewrite. "No exceptions left" has now
been written and then falsified twice, which argues for the generated axes over the enumeration:
the probes keep the claim true, and the list does not.

The first two exceptions were the same one. A `depends_on` naming an undefined service, and a
dependency cycle, among services outside the target's closure were rejected by Docker and accepted
here ([#87](https://github.com/modern-python/compose2pod/issues/87)). Both checks lived in the
closure walk `--target` drives, and a service nothing targets is never walked.
`graph.validate_graph` walks from every service instead, so the verdict on the document no longer
depends on which service the caller asked to run. That is Docker's own behaviour, and the only
reading the hard rule allows. Its price is the reason the issue stayed open, and the fix does not
hide it: one service's typo now refuses the file for every target in it. Both documents moved out
of a residual catalogue and into `tests/conformance/corpus/`, where `assert_rule`, which raises on
exactly that combination, holds them closed. The rule is hard everywhere, with nothing suspended.

One over-rejection is settled: the drive-qualified bind (`C:\data:/var`). podman mounts that source
through `--mount`, and only the short `-v` spec cannot spell it, so the capability is reachable
today. The long form emits exactly the invocation measured at exit 0, and a `LIMITATIONS` row
asserts it continuously. What was left was the ergonomic question, and
[#111](https://github.com/modern-python/compose2pod/issues/111) closed it as working as intended.
On Linux that spelling overwhelmingly means a Windows user pointed at a drive that is not there.
Falling back to `--mount` would bind a relative directory literally named `C:\data`, which would
surface much later as an empty mount; the refusal fails at once with an error naming the long form.
So the refusal is the intended behaviour, not a gap waiting to be filled. What would reopen it is a
real document wanting a colon in a short-form bind source on Linux, which is the evidence
[#86](https://github.com/modern-python/compose2pod/issues/86) never had.

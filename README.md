<a href="https://mini-swe-agent.com/latest/"><img src="https://github.com/SWE-agent/mini-swe-agent/raw/main/docs/assets/mini-swe-agent-banner.svg" alt="mini-swe-agent banner" style="height: 7em"/></a>

# Mytilus Status

This document records the current Mytilus integration state in this repository, the changes made to adapt mini-swe-agent to Mytilus/mytilus, and the commands used to reproduce benchmark runs.

## Solution Transcript

We reproduce the solution of example `swe-agent__test-repo-1`. The log is, conveniently, a single YAML document that is readily runnable, inspectable, and gives a reproducible description of a set of system commands that lead to the solution.

```yaml
- !ls { -R, . }
- !cat tests/missing_colon.py
- !python3 tests/missing_colon.py
- - !echo |
        #!/usr/bin/env python3
        def division(a: float, b: float) -\u003e float:
            return a/b
        if __name__ == \"__main__\":
            print(division(123, 15))
  - !tee tests/missing_colon.py
- !cat tests/missing_colon.py
- !sed { -i, 's/def division(a: float, b: float) -\u003e float/def division(a: float, b: float) -\u003e float:/', tests/missing_colon.py }
- !cat tests/missing_colon.py
- !python3 tests/missing_colon.py
- !cat src/testpkg/missing_colon.py
- !sed { -i, 's/def division(a: float, b: float) -\u003e float/def division(a: float, b: float) -\u003e float:/', src/testpkg/missing_colon.py }
- !python3 src/testpkg/missing_colon.py
- !grep { -r, \"def .* -\u003e [^:]*$\", . }
- !cat tests/existing_lint_error.py
- !sed { -i, 's/def division(a: float, b: float) -\u003e float/def division(a: float, b: float) -\u003e float:/', tests/existing_lint_error.py }
- !python3 tests/existing_lint_error.py
- !grep { -r, \"def .* -\u003e [^:]*$\", . }
- !python3 tests/missing_colon.py
- !echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
```

## Current State

The repository currently supports running SWE-bench with a Mytilus-backed command environment.

Key points:

- Benchmark containers can be patched to install `mytilus`/`mytilus` and expose a `mytilus` entrypoint.
- Mytilus benchmark configs exist for both tool-call and text-based model flows.
- Mytilus is treated as the only command language in the mytilus-facing prompts.
- Shell access is only intended through Mytilus syntax, specifically `!bash { -c, "..." }`.
- Mytilus tool-call validation now rejects raw shell-shaped payloads and malformed YAML before execution.
- Benchmark logging now includes command execution lines before each action runs.

Primary files:

- [SKILL.md](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/SKILL.md)
- [docker.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/environments/docker.py)
- [swebench_mytilus_textbased.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/benchmarks/swebench_mytilus_textbased.yaml)
- [swebench_mytilus.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/benchmarks/swebench_mytilus.yaml)
- [default_mytilus.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/default_mytilus.yaml)
- [actions_toolcall.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/models/utils/actions_toolcall.py)
- [default.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/agents/default.py)

## Changelog

### 1. Benchmark config and prompt setup

- Added a dedicated text-based benchmark config for mytilus/Mytilus:
  - [swebench_mytilus_textbased.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/benchmarks/swebench_mytilus_textbased.yaml)
- Registered mytilus benchmark config documentation in:
  - [README.md](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/README.md)

### 2. Docker image patching for mytilus/mytilus

- Added Docker patching logic that detects mytilus interpreter usage and builds a patched benchmark image.
- The patched image now:
  - installs `python3-venv`
  - creates `/opt/mytilus-venv`
  - installs dependency layers before copying the full mytilus source for better caching
  - installs the full source with `pip install --no-deps -e /mytilus_src`
  - writes `/usr/local/bin/mytilus`
- Docker dependency caching was improved by:
  - copying only `pyproject.toml` first
  - extracting `project.dependencies`
  - installing those deps before copying the whole source tree

Relevant files:

- [docker.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/environments/docker.py)
- [test_docker.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/tests/environments/test_docker.py)

### 3. Python compatibility fix for mytilus runtime

- Earlier failures showed `nx_hif` required Python 3.12 syntax.
- The patched image path was updated to use a dedicated Python venv suitable for Mytilus/mytilus in the benchmark image.

### 4. Retry and quota handling

- Retry logic now distinguishes:
  - retryable rate limits
  - exhausted quota conditions
- Provider-supplied retry delays are honored when available.
- Added `QuotaExceededError`.

Relevant files:

- [retry.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/models/utils/retry.py)
- [exceptions.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/exceptions.py)
- [litellm_model.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/models/litellm_model.py)
- [litellm_textbased_model.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/models/litellm_textbased_model.py)
- [litellm_response_model.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/models/litellm_response_model.py)

### 5. Logging improvements

- Benchmark log files now attach at the root logger, so non-`minisweagent.*` logs appear in the run log.
- The agent now logs each action before execution:
  - `Executing action 1/N: ...`
- This prints the command call into stdout/logging during benchmarks without printing the response body at that same logging point.

Relevant files:

- [log.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/utils/log.py)
- [default.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/agents/default.py)
- [test_default_logging.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/tests/agents/test_default_logging.py)

### 6. Prompt and skill migration to Mytilus-first behavior

- Added and expanded repo-local Mytilus guidance in:
  - [SKILL.md](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/SKILL.md)
- Mytilus-facing prompts were tightened so they now explicitly state:
  - Mytilus is the only command language
  - commands must be valid YAML/Mytilus documents
  - plain shell text such as `bash -c "ls -la"` is invalid
  - bash is available only through `!bash { -c, "..." }`

Updated configs include:

- [default_mytilus.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/default_mytilus.yaml)
- [mini_mytilus.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/mini_mytilus.yaml)
- [mini_mytilus_textbased.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/mini_mytilus_textbased.yaml)
- [swebench_mytilus.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/benchmarks/swebench_mytilus.yaml)
- [swebench_mytilus_xml.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/benchmarks/swebench_mytilus_xml.yaml)
- [swebench_mytilus_textbased.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/benchmarks/swebench_mytilus_textbased.yaml)

### 7. Tool schema and action validation for mytilus

- Mytilus tool schema descriptions now say the tool expects a Mytilus YAML document.
- Tool-call parsing now rejects:
  - raw shell text
  - commands not starting with `!`, `-`, or `?`
  - malformed YAML
- Validation is applied before execution.

Relevant files:

- [actions_toolcall.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/models/utils/actions_toolcall.py)
- [actions_toolcall_response.py](c:/Users/marti/Github/SWE-agent/mini-swe-agent/src/minisweagent/models/utils/actions_toolcall_response.py)
- [test_actions_toolcall.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/tests/models/test_actions_toolcall.py)

### 8. Text-based Mytilus fence name

- The bash text-based path still retains its existing fence name.
- A separate Mytilus-specific fence name was introduced:
  - ````mswea_mytilus_command`
- The mytilus text-based configs now use:
  - `action_regex: '```mswea_mytilus_command\s*\n(.*?)\n```'`

Relevant files:

- [mini_mytilus_textbased.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/mini_mytilus_textbased.yaml)
- [swebench_mytilus_textbased.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/benchmarks/swebench_mytilus_textbased.yaml)
- [default_mytilus.yaml](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/src/minisweagent/config/default_mytilus.yaml)

## Known Limitations

- Benchmark success still depends heavily on model compliance with Mytilus prompt instructions.
- Some old generic bash-oriented configs still exist in the repository; they are separate modes and not Mytilus modes.
- The broad [test_default.py](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/tests/agents/test_default.py) suite is currently failing in this workspace for reasons not isolated as part of the Mytilus logging/fence changes, so validation here relies on focused tests.

## Migration Pain Points

Moving from a bash-only system to a repository that supports both bash and Mytilus exposed several structural assumptions.

### 1. "Command" originally meant "bash string"

Large parts of the codebase assumed that:

- one action equals one shell string
- shell syntax is the native authoring model
- `bash -c` is the universal fallback

That assumption was embedded in:

- prompt examples
- format-error templates
- text-based regex extraction
- tool descriptions
- execution mental model

In a Mytilus world, the action is not "a shell string". It is a structured YAML document. That changes both validation and prompting.

### 2. Prompt and parser drift

The repository had multiple independent places describing action format:

- system prompts
- instance prompts
- format-error templates
- text-based extraction regexes
- tool schemas
- tests with hardcoded fences

When one changed but the others did not, the model could still emit legacy shell-shaped content. This happened repeatedly with:

- `bash -c "..."` outputs
- plain `ls -la`
- malformed pseudo-Mytilus
- old custom fence names

The main lesson is that prompt syntax, parser syntax, and tests must move together.

### 3. Validation used to happen too late

Originally, many malformed commands were only discovered:

- by the container
- by Mytilus parsing
- by subprocess execution

That is too late. By that point the model has already "succeeded" in producing an action. The migration required moving validation earlier into:

- tool-call parsing
- command argument validation
- YAML syntax validation
- prompt-level format correction

### 4. Text-based modes hardcoded fence names

The text-based model path assumed fixed fence labels such as:

- `mswea_bash_command`

This made Mytilus support awkward because the parser and the prompt were coupled through hardcoded strings. The current repository now preserves the bash fence path while adding a separate Mytilus fence path:

- `mswea_bash_command`
- `mswea_mytilus_command`

This is still an intermediate state, not the final abstraction.

### 5. Environment abstraction is still shell-biased

Even when Mytilus is the logical command language, the actual runtime stack still contains shell-biased concepts:

- `interpreter: ["bash", "-lc"]` as the default mental model
- environment docs speaking in terms of "subshells"
- `shell=True` in some extra environments
- execution APIs taking plain `command: str`

This does not block Mytilus support, but it means the repository has not yet fully generalized from "shell executor" to "command runtime".

### 6. Two-shell support raises configuration duplication

Once bash and Mytilus both exist, every new config or prompt risks duplication:

- bash config
- mytilus config
- tool-call version
- text-based version
- XML version

Without a stronger abstraction, every prompt improvement has to be copied into multiple files.

### 7. "Only use bash through Mytilus" is semantically stricter than "support two shells"

There are two different goals:

1. support more than one runtime in the repository
2. in a given Mytilus run, force the model to think only in Mytilus and use bash only as an embedded tagged node

Those are not the same problem.

The current repository mostly addresses the second goal for mytilus-facing configs, but the first goal is only partially abstracted.

## Continuing Toward Arbitrary Shell / Runtime Support

The longer-term target should not be "support bash and Mytilus as special cases". It should be:

- the agent chooses or is configured with a command runtime
- the runtime defines its own authoring format
- the runtime defines its own validation rules
- the runtime defines how scripts/files are generated and executed

In that model, Mytilus is one runtime, bash is another, and others could be added later.

### Desired end state

The repository should evolve toward a runtime abstraction with at least these first-class fields:

- runtime name
- action format
- parser / extractor
- validator
- execution interpreter
- tool schema description
- prompt snippets
- script file conventions

That would let the system describe:

- bash: shell string runtime
- Mytilus: YAML document runtime
- Python REPL/runtime
- PowerShell runtime
- Nushell runtime
- domain-specific runtimes

### What to generalize next

#### 1. Separate "runtime" from "tool name"

Right now the repository often overloads:

- tool name
- interpreter
- prompt language
- parser expectations

Instead, define an explicit runtime concept, for example:

- `runtime.name`
- `runtime.tool_name`
- `runtime.action_regex`
- `runtime.interpreter`
- `runtime.validation_mode`

This would remove a lot of ad hoc branching around `command_tool_name == "mytilus"`.

#### 2. Make action validation pluggable

Current validation is partially special-cased for mytilus. The next step should be a runtime validator interface:

- bash validator: accept shell string
- Mytilus validator: parse YAML, enforce tagged nodes, reject raw shell text
- future runtimes: implement their own validator

This makes "arbitrary shell/runtime support" a matter of adding a validator rather than sprinkling special cases.

#### 3. Make prompt snippets runtime-owned

Prompt instructions should be assembled from runtime-specific prompt fragments, rather than copied into many config files.

For example:

- runtime overview
- valid action examples
- invalid action examples
- formatting examples
- format-error guidance

Then configs can select a runtime instead of embedding all runtime text inline.

#### 4. Make fenced text extraction runtime-owned

For text-based models, action extraction should come from the runtime definition:

- bash runtime => `mswea_bash_command`
- Mytilus runtime => `mswea_mytilus_command`
- future runtime => its own fence/tag

Longer-term, even the fence name should be configurable by runtime metadata rather than hardcoded strings inside model classes.

#### 5. Add runtime-native script generation

To support "letting the agent generate the script and files in any language the shell uses", the agent needs a clearer model of script artifacts.

That means supporting patterns like:

- generate a shell script file and run it
- generate a Python helper and invoke it
- generate a PowerShell script and run it
- generate a Mytilus YAML program file and run it

The repository should treat these as explicit runtime-native workflows, not as ad hoc inline blobs.

### Recommended execution model for arbitrary runtime support

The clean model is:

1. The runtime defines the authoring language for the action.
2. The agent emits an action in that language.
3. The validator checks syntax and runtime-specific invariants before execution.
4. The runtime executor decides whether the action is:
   - inline
   - saved to a file first
   - composed from multiple files
5. The executor runs it with the configured interpreter.

Examples:

- bash runtime:
  - action is a shell string
  - executor may run `bash -lc "<command>"`
- Mytilus runtime:
  - action is a YAML document
  - executor runs `mytilus -c "<yaml-doc>"`
- Python-script runtime:
  - action is Python source
  - executor writes `script.py` then runs `python script.py`

### Supporting generated files and multi-language helper code

To let the agent generate scripts and files in any language the runtime uses, the repository should explicitly support artifact-producing actions.

For example, the action schema should conceptually allow:

- inline command documents
- script file creation
- helper file creation
- execution of generated artifacts

For Mytilus, this is already partially natural because the runtime can express:

- `!echo | ...`
- `!tee file`
- `!python script.py`
- `!bash { -c, "..." }`

But for general runtime support, the repository should stop assuming that all useful work can be squeezed into one opaque string command.

### Practical next steps

High-value next steps from here:

1. Introduce a `runtime` abstraction in model and config layers.
2. Move mytilus-specific validation into a generic runtime validator registry.
3. Move prompt fragments into reusable runtime-specific templates or include files.
4. Replace repeated inline mytilus prompt copies with shared Mytilus prompt content.
5. Add a runtime-aware text fence helper so fence names are config/runtime data, not scattered strings.
6. Add tests for:
   - bash runtime
   - Mytilus runtime
   - text-based parsing per runtime
   - tool-call validation per runtime
   - generated script-file workflows

### Short version

The hard part of this migration is not "support one more shell". The hard part is removing the assumption that the agent's action is always "a bash string".

Once the repository models actions as runtime-defined programs instead of shell strings, supporting:

- Mytilus
- bash
- arbitrary other shells
- script-generating workflows

becomes much cleaner.

## Reproducing the Benchmarks

All commands below assume repo root:

`c:\Users\marti\GitHub\SWE-agent\mini-swe-agent`

### Environment setup

PowerShell:

```powershell
$env:PYTHONPATH='src'
$env:PYTHONIOENCODING='utf-8'
```

### 1. Run a one-instance Mytilus smoke benchmark

```powershell
.venv\Scripts\python -m minisweagent.run.benchmarks.swebench --subset _test --split test --slice 0:1 --workers 1 -c swebench_mytilus_textbased -o results\mytilus-smoke
```

Equivalent entrypoint if using the extra CLI:

```powershell
mini-extra swebench -c swebench_mytilus_textbased --subset _test --split test --slice 0:1 --workers 1 -o results\mytilus-smoke
```

### 2. Run the full mytilus text-based benchmark config on a slice

```powershell
.venv\Scripts\python -m minisweagent.run.benchmarks.swebench --split test --slice 0:10 --workers 1 -c swebench_mytilus_textbased -o results\mytilus-batch
```

### 3. Run the non-text-based mytilus benchmark config

```powershell
.venv\Scripts\python -m minisweagent.run.benchmarks.swebench --split test --slice 0:10 --workers 1 -c swebench_mytilus -o results\mytilus-toolcall
```

### 4. Single-instance debug run

```powershell
.venv\Scripts\python -m minisweagent.run.benchmarks.swebench_single --instance_id swe-agent__test-repo-1 -c swebench_mytilus_textbased -o results\mytilus-single
```

## Credentials

If using Anthropic through LiteLLM:

```powershell
$env:ANTHROPIC_API_KEY='your-key'
```

If using Gemini through LiteLLM:

```powershell
$env:GEMINI_API_KEY='your-key'
```

Adjust the model in the config if needed.

## Useful Logs and Outputs

During benchmark runs, inspect:

- benchmark log:
  - [minisweagent.log](c:/Users/marti/GitHub/SWE-agent/mini-swe-agent/results/mytilus04/minisweagent.log)
- per-instance trajectory:
  - `results/<run>/<instance>/<instance>.traj.json`
- predictions:
  - `results/<run>/preds.json`
- exit statuses:
  - `results/<run>/exit_statuses_<timestamp>.yaml`

The command-execution trace should now appear in logs/stdout as lines like:

```text
Executing action 1/1: !ls -la
```

## Validation Commands

Focused tests used during the Mytilus migration:

```powershell
.venv\Scripts\python -m pytest tests/config/test_init.py
.venv\Scripts\python -m pytest tests/environments/test_docker.py -k mytilus_dockerfile_uses_dedicated_python_env
.venv\Scripts\python -m pytest tests/models/test_actions_toolcall.py
.venv\Scripts\python -m pytest tests/models/test_retry.py
.venv\Scripts\python -m pytest tests/utils/test_log.py
.venv\Scripts\python -m pytest tests/agents/test_default_logging.py
```

## Notes

- The repo worktree may contain additional unrelated edits.
- This document is intended as a repository-local integration note, not a formal release note.

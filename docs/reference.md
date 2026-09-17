# Superwait reference

The [full API and CLI reference](../src/superwait/references/api.md) is also
installed beside the skill as `references/api.md`, so agents can read it locally
only when needed.

## Install from source

To install the current source instead of the PyPI release:

```sh
uv tool install 'git+https://github.com/ctxrs/superwait'
```

## Python and package installation

[uv](https://docs.astral.sh/uv/getting-started/installation/) is the recommended
installer. It creates a persistent, isolated tool environment and can download
Python automatically. You do not need to set up Python or a virtual environment
yourself. To explicitly select a runtime for the published package:

```sh
uv tool install --python 3.12 superwait
```

The [README](../README.md#install) covers host-wide setup, available in 0.3.0
and newer. Upgrade an existing uv installation with `uv tool upgrade superwait`.

If you already manage Python 3.11+ with pipx, `pipx install superwait` is another
option. Keep whichever tool environment you install: the generated MCP and hook
commands point at its Python executable. A temporary `uvx` run is not the
recommended setup path because its cached environment can be removed.

If `superwait` is not found after installation, follow uv's printed PATH
instructions or run `uv tool update-shell` and open a new terminal. Setup itself
does not edit shell profiles.

Sources: [uv tool environments](https://docs.astral.sh/uv/guides/tools/),
[automatic Python downloads](https://docs.astral.sh/uv/guides/install-python/#automatic-python-downloads).

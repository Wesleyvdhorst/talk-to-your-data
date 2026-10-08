# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This directory is not a git repository and contains a single script, `hello_world.py`. There is no package manifest, dependency list, test suite, linter config, or build step yet. Update this file once the project grows.

## Running code

```bash
python3 hello_world.py
```

Python is managed with `uv`. Python 3.14 is installed as the default `python3` in `~/.local/bin`, which `~/.zshrc` puts ahead of the macOS system Python 3.9 at `/usr/bin/python3`. A shell started before that PATH change may still pick up 3.9; use `uv run --python 3.14 <script>` to pin the version explicitly.

#!/usr/bin/env python3
"""Execute a command with variables loaded from a dotenv-compatible env file.

The production environment file is shared with systemd. Keeping deployment
scripts from `source`-ing secret files avoids shell interpretation of password
characters and keeps secret values out of command output.
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import dotenv_values


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a command with variables from an environment file.")
    parser.add_argument("env_file")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    values = dotenv_values(args.env_file)
    env = os.environ.copy()
    for key, value in values.items():
        if key and value is not None:
            env[str(key)] = str(value)

    os.execvpe(command[0], command, env)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())

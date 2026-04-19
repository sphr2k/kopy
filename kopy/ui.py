from __future__ import annotations

import sys

from rich.console import Console


console = Console(soft_wrap=True)


def pick_with_fzf(items: list[str], prompt: str, multi: bool = False) -> list[str]:
    try:
        from iterfzf import iterfzf
    except ModuleNotFoundError:
        console.print("[red]Missing dependency 'iterfzf'. Run: uv sync[/red]")
        return []

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return []

    result = iterfzf(items, prompt=prompt, multi=multi)
    if not result:
        return []
    if isinstance(result, str):
        return [result]
    return [item for item in result if isinstance(item, str)]

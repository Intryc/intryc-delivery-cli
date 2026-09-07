import json

import click

from intryc_delivery.errors import Issue


def report(
    command: str, outcome: str, *, json_output: bool, issues: list[Issue] | None = None, **details
):
    payload = {
        "result_version": "1.0",
        "command": command,
        "outcome": outcome,
        **details,
        "issues": [
            i.model_dump(exclude_none=True) for i in sorted(issues or [], key=Issue.sort_key)
        ],
    }
    if json_output:
        click.echo(json.dumps(payload, sort_keys=True))
    else:
        click.echo(f"{command}: {outcome}")
        for key, value in details.items():
            click.echo(f"  {key}: {value}")
        for issue in payload["issues"]:
            location = " ".join(str(issue.get(k) or "") for k in ("path", "json_pointer")).strip()
            click.echo(
                f"  {issue['code']} {location}: {issue['message']} {issue['corrective_action']}"
            )

from pathlib import Path

import yaml


def test_deploy_retries_and_uses_successful_deployment_url():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/signup.yml").read_text())
    deploy = workflow["jobs"]["deploy"]
    steps = deploy["steps"]
    first = next(step for step in steps if step.get("id") == "deployment")
    retry = next(step for step in steps if step.get("id") == "retry_deployment")
    wait = steps[steps.index(retry) - 1]

    assert first["uses"] == retry["uses"] == "actions/deploy-pages@v4"
    assert first["continue-on-error"] is True
    assert wait["if"] == retry["if"] == "steps.deployment.outcome == 'failure'"
    assert wait["run"] == "sleep 10"
    assert "continue-on-error" not in retry
    assert deploy["environment"]["url"] == (
        "${{ steps.deployment.outputs.page_url || steps.retry_deployment.outputs.page_url }}"
    )

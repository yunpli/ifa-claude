import json

from typer.testing import CliRunner

from ifa.cli.sme import _print_json, app


def test_sme_cli_help_loads():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Smart Money Enhanced" in result.output


def test_incremental_help_includes_production_contract_flags():
    result = CliRunner().invoke(app, ["etl", "incremental", "--help"])
    assert result.exit_code == 0
    assert "--source-mode" in result.output
    assert "--force" in result.output
    # Rich may truncate the long positive option in narrow test output, but the
    # paired negative flag stays visible and exercises the same Typer option.
    assert "--allow-core-missing" in result.output


def test_validate_backfill_help_loads():
    result = CliRunner().invoke(app, ["etl", "validate-backfill", "--help"])
    assert result.exit_code == 0
    assert "Validate historical SME backfill" in result.output


def test_market_structure_help_loads():
    result = CliRunner().invoke(app, ["market-structure", "--help"])
    assert result.exit_code == 0
    assert "Explain daily market structure" in result.output
    assert "--client" in result.output
    assert "--llm-narrative" in result.output
    assert "--persist" in result.output


def test_brief_help_loads():
    result = CliRunner().invoke(app, ["brief", "--help"])
    assert result.exit_code == 0
    assert "conclusion-only" in result.output
    assert "--format" in result.output


def test_compute_market_structure_help_loads():
    result = CliRunner().invoke(app, ["compute", "market-structure", "--help"])
    assert result.exit_code == 0
    assert "Persist market-structure snapshots" in result.output


def test_compute_strategy_eval_help_loads():
    result = CliRunner().invoke(app, ["compute", "strategy-eval", "--help"])
    assert result.exit_code == 0
    assert "Join persisted strategy snapshots" in result.output


def test_tuning_ready_help_loads():
    result = CliRunner().invoke(app, ["tuning-ready", "--help"])
    assert result.exit_code == 0
    assert "enough persisted outcomes" in result.output


def test_tune_bucket_review_help_loads():
    result = CliRunner().invoke(app, ["tune", "bucket-review", "--help"])
    assert result.exit_code == 0
    assert "outcome-first tuning artifact" in result.output


def test_tune_promote_profile_help_loads():
    result = CliRunner().invoke(app, ["tune", "promote-profile", "--help"])
    assert result.exit_code == 0
    assert "promote it into YAML" in result.output


def test_print_json_is_machine_parseable(capsys):
    _print_json({"text": "这是一段很长的中文解释，用来确保 JSON 输出不会被 Rich 在字符串内部自动换行。"})
    out = capsys.readouterr().out
    assert json.loads(out)["text"].startswith("这是一段")

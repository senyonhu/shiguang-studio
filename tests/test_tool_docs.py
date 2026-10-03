"""Keep author-facing examples consistent with actual public MCP input schemas."""
import copy
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, ValidationError
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = json.loads((ROOT / "docs/tool-examples.json").read_text(encoding="utf-8"))
TOOLS = {tool["name"]: tool for tool in json.loads((ROOT / "docs/tools.schema.json").read_text(encoding="utf-8"))["tools"]}


@pytest.mark.parametrize("example", EXAMPLES, ids=[value["tool"] for value in EXAMPLES])
def test_examples_follow_public_tool_schema(example):
    Draft202012Validator(TOOLS[example["tool"]]["inputSchema"]).validate(example["arguments"])


def test_skill_examples_match_validated_fixtures():
    source = (ROOT / "plugin/renpy-bridge/skills/renpy-author/references/objects.md").read_text(encoding="utf-8")
    blocks = [json.loads(value) for value in re.findall(r"```json\s*\n(.*?)\n```", source, re.DOTALL)]
    assert blocks == [example["arguments"] for example in EXAMPLES]


def test_public_schema_rejects_observed_wrong_asset_fields():
    example = copy.deepcopy(EXAMPLES[0]["arguments"])
    example["assets"][0]["type"] = example["assets"][0].pop("kind")
    with pytest.raises(ValidationError):
        Draft202012Validator(TOOLS["register_assets"]["inputSchema"]).validate(example)


def test_public_schema_rejects_observed_shorthand_flow():
    example = copy.deepcopy(EXAMPLES[1]["arguments"])
    example["frames"][0]["flow"] = {"end": True}
    with pytest.raises(ValidationError):
        Draft202012Validator(TOOLS["apply_frame_batch"]["inputSchema"]).validate(example)


def test_public_schema_rejects_unsupported_character_provenance():
    example = copy.deepcopy(EXAMPLES[1]["arguments"])
    example["characters"][0]["provenance"] = {"source_id": "source_example"}
    with pytest.raises(ValidationError):
        Draft202012Validator(TOOLS["apply_frame_batch"]["inputSchema"]).validate(example)

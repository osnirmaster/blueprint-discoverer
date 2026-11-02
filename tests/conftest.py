from pathlib import Path

import pytest

from blueprint_discoverer.pipeline import Pipeline
from blueprint_discoverer.workspace import ProjectConfig, Workspace

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "banking-landscape"


@pytest.fixture(scope="session")
def pipeline(tmp_path_factory) -> Pipeline:
    ws = Workspace(tmp_path_factory.mktemp("bp"))
    p = Pipeline(ProjectConfig.load(EXAMPLE / "blueprint.yaml"), ws, EXAMPLE)
    p.report = p.run(use_llm=False)
    return p

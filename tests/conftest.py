import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from healthlab import build, parse  # noqa: E402
import synthetic  # noqa: E402


@pytest.fixture(scope="session")
def lab(tmp_path_factory):
    root = tmp_path_factory.mktemp("lab")
    xml = root / "export.xml"
    truth = synthetic.generate(xml)
    parse.ingest(str(xml), root)
    build.build(root)
    return root, truth, xml

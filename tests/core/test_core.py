"""bpy-free helpers of the FEA half (blendfea/fea/core.py)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "examples"))

from blendfea.fea import core, fxclient  # noqa: E402
import blendfea  # noqa: E402


@pytest.fixture(scope="module")
def beam_msh(tmp_path_factory):
    from make_cantilever import make
    p = tmp_path_factory.mktemp("mesh") / "beam.msh"
    make(str(p), 100.0, 10.0, 10.0, 2.5)
    return str(p)


def test_groups_and_colours(beam_msh):
    names = [n for d, n in core.read_msh_groups(beam_msh) if d == 2]
    assert set(names) == {"fixed", "tip", "top", "_untagged"}
    c = core.sf_colors(np.array([0.5, 1.0, 2.0, 10.0]), 2.0)
    assert c[0, 0] > c[0, 1] and c[-1, 1] > c[-1, 0]       # red low, green high


def test_bbox_read_from_file(beam_msh):
    assert np.allclose(core.read_msh_bbox(beam_msh), [100.0, 10.0, 10.0])


def test_unit_suggestion():
    assert core.suggest_units([0.1, 0.01, 0.01], "mm") == "m"    # Blender metres read as mm
    assert core.suggest_units([100, 10, 10], "mm") is None
    assert core.suggest_units([10, 100, 10], "m") == "mm"       # mm part read as metres
    assert core.suggest_units([1e4, 1e5, 1e4], "m") is None     # nothing plausible


def test_client_api_supported():
    """The vendored fxclient must speak a server API this release supports."""
    assert fxclient.API_VERSION in blendfea.SUPPORTED_SERVER_API

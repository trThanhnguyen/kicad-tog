"""Tests for project.py: which nets get checked.

--nets wins; else the non-Default net classes of the .kicad_pro; else pair
names; else an error. Never a silent pass with nothing checked.
"""

import json

import pytest

from kicad_tog.project import classes_of, load_net_classes, pair_nets, select_nets

BOARD_NETS = {
    "GND", "+3V3",
    "/CM5/PCIE_PI.RX_P", "/CM5/PCIE_PI.RX_N",
    "/CM5/HDMI_PI.D0_P", "/CM5/HDMI_PI.D0_N",
    "/USB_C.D_P", "/USB_C.D_N",
    "/CSI/CLK+", "/CSI/CLK-",
    "/LED_P",                       # no partner: not a pair
}


def write_project(tmp_path, patterns=(), assignments=None):
    """A minimal .kicad_pro with the two net_settings fields we read."""
    path = tmp_path / "board.kicad_pro"
    path.write_text(json.dumps({"net_settings": {
        "classes": [{"name": "Default"}],
        "netclass_patterns": [{"pattern": p, "netclass": c} for p, c in patterns],
        "netclass_assignments": assignments,
    }}), encoding="utf-8")
    return path


# ---- reading the project file ----

def test_missing_project_file_has_no_classes(tmp_path):
    assert load_net_classes(tmp_path / "nope.kicad_pro") == ([], {})


def test_assignment_values_become_lists(tmp_path):
    # KiCad 8+ writes lists; accept a plain string too
    project = write_project(tmp_path, assignments={"A": ["90ohm"], "B": "100ohm"})

    assert load_net_classes(project)[1] == {"A": ["90ohm"], "B": ["100ohm"]}


def test_classes_come_from_patterns_and_assignments():
    patterns = [("/*PCIE*", "90ohm"), ("/*HDMI_P*.D*", "100ohm")]
    assignments = {"/CM5/PCIE_PI.RX_P": ["85Ohm-diff_PCIE"]}

    assert classes_of("/CM5/PCIE_PI.RX_P", patterns, assignments) == ["85Ohm-diff_PCIE", "90ohm"]
    assert classes_of("/CM5/HDMI_PI.D0_N", patterns, assignments) == ["100ohm"]
    assert classes_of("GND", patterns, assignments) == []


# ---- --nets given ----

def test_given_names_are_used_as_is(tmp_path):
    assert select_nets(BOARD_NETS, ["/USB_C.D_P"], tmp_path / "x.kicad_pro") == (["/USB_C.D_P"], None)


def test_given_wildcards_match_board_nets(tmp_path):
    nets, why = select_nets(BOARD_NETS, ["/CM5/PCIE*", "/USB_C.D_P"], tmp_path / "x.kicad_pro")

    assert nets == ["/CM5/PCIE_PI.RX_N", "/CM5/PCIE_PI.RX_P", "/USB_C.D_P"]
    assert why is None


def test_unknown_name_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="SGI"):
        select_nets(BOARD_NETS, ["SGI"], tmp_path / "x.kicad_pro")


def test_wildcard_matching_nothing_is_an_error(tmp_path):
    with pytest.raises(ValueError, match=r"/\*DDR\*"):
        select_nets(BOARD_NETS, ["/*DDR*"], tmp_path / "x.kicad_pro")


def test_given_nets_win_over_net_classes(tmp_path):
    project = write_project(tmp_path, patterns=[("/*PCIE*", "90ohm")])

    assert select_nets(BOARD_NETS, ["GND"], project) == (["GND"], None)


# ---- from the project's net classes ----

def test_non_default_net_classes_are_selected(tmp_path):
    project = write_project(
        tmp_path,
        patterns=[("/*PCIE*", "90ohm"), ("/*HDMI_P*.D*", "100ohm"), ("GND", "Default")],
        assignments={"/USB_C.D_P": ["90ohm"], "/USB_C.D_N": ["Default"]},
    )
    nets, why = select_nets(BOARD_NETS, None, project)

    assert nets == sorted(["/CM5/PCIE_PI.RX_P", "/CM5/PCIE_PI.RX_N",
                           "/CM5/HDMI_PI.D0_P", "/CM5/HDMI_PI.D0_N", "/USB_C.D_P"])
    assert why == "net classes: 100ohm: 2, 90ohm: 3"


def test_a_class_missing_from_the_class_list_still_counts(tmp_path):
    # Jetson Thor's project assigns 40 nets to a class it no longer defines
    project = write_project(tmp_path, assignments={"/CSI/CLK+": ["95Ohm-diff_CSI"]})

    assert select_nets(BOARD_NETS, None, project)[0] == ["/CSI/CLK+"]


# ---- fallback: pair names ----

def test_pair_names_need_their_partner():
    assert pair_nets(BOARD_NETS) == sorted([
        "/CM5/PCIE_PI.RX_P", "/CM5/PCIE_PI.RX_N", "/CM5/HDMI_PI.D0_P", "/CM5/HDMI_PI.D0_N",
        "/USB_C.D_P", "/USB_C.D_N", "/CSI/CLK+", "/CSI/CLK-",
    ])


def test_without_net_classes_pairs_are_used(tmp_path):
    project = write_project(tmp_path, patterns=[("GND", "Default")])
    nets, why = select_nets(BOARD_NETS, None, project)

    assert "/USB_C.D_P" in nets and "/LED_P" not in nets
    assert why == "pair names, no net classes"


def test_nothing_selected_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="no nets selected"):
        select_nets({"GND", "SIG", "/LED_P"}, None, tmp_path / "x.kicad_pro")

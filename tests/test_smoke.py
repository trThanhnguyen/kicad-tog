import pytest
from shapely.geometry import LineString

def test_shapely_length():
    line = LineString([[0,0], [3,4]])
    assert line.length == pytest.approx(5.0)
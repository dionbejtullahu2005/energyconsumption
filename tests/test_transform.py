from enerco_analysis.transform import _chunks

def test_chunks_preserve_all_rows() -> None:
    result = list(_chunks(iter(range(7)), 3))
    assert result == [[0, 1, 2], [3, 4, 5], [6]]

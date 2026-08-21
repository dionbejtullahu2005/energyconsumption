from enerco_analysis.clustering import _elbow_k, _make_scaler

def test_elbow_choice_is_internal_for_curved_inertia() -> None:
    assert _elbow_k([2, 3, 4, 5, 6], [100.0, 55.0, 35.0, 28.0, 24.0]) in {3, 4}

def test_scaler_factory() -> None:
    assert _make_scaler("standard").__class__.__name__ == "StandardScaler"

from app.comps import compute_stats


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats.avg_price is None
    assert stats.median_price is None
    assert stats.sample_count == 0


def test_compute_stats_small_sample_no_trim():
    # fewer than 5 samples: average uses all of them, no outlier trimming
    stats = compute_stats([100, 200, 300])
    assert stats.sample_count == 3
    assert stats.median_price == 200
    assert stats.avg_price == 200


def test_compute_stats_trims_high_and_low_outlier():
    # 5+ samples: single highest and lowest are dropped before averaging
    prices = [50, 100, 100, 100, 1000]
    stats = compute_stats(prices)
    assert stats.sample_count == 5
    assert stats.median_price == 100
    # trimmed set is [100, 100, 100] -> avg 100, vs. untrimmed avg of 270
    assert stats.avg_price == 100


def test_compute_stats_median_unaffected_by_trim():
    prices = [10, 20, 30, 40, 1000]
    stats = compute_stats(prices)
    assert stats.median_price == 30
    assert stats.avg_price == 30  # trimmed set [20, 30, 40]

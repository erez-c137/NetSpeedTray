from netspeedtray.views.graph.worker import GraphDataWorker


def test_preserve_global_peaks_keeps_upload_and_download_extrema():
    data = []
    for i in range(100):
        up = float(i)
        down = float(100 - i)
        if i == 73:
            down = 1000.0
        data.append((float(i), up, down))

    sampled = data[::10]
    result = GraphDataWorker._preserve_global_peaks(data, sampled)

    assert (99.0, 99.0, 1.0) in result
    assert (73.0, 73.0, 1000.0) in result
    assert result == sorted(result, key=lambda point: point[0])

from PySide6.QtCore import QCoreApplication, QTimer

from modbus_tool.core.serial_worker import SerialWorker


def test_serial_worker_queue_loopback_and_clean_stop():
    app = QCoreApplication.instance() or QCoreApplication([])
    frames = []
    counts = []
    worker = SerialWorker(
        {
            "port": "loop://",
            "baudrate": 115200,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
        }
    )
    worker.frame_received.connect(frames.append)
    worker.data_received.connect(counts.append)
    worker.status_changed.connect(
        lambda connected, _detail: worker.send(b"\x01\x03\x00\x00") if connected else None
    )
    QTimer.singleShot(300, app.quit)
    worker.start()
    app.exec()
    worker.stop()
    assert frames == [b"\x01\x03\x00\x00"]
    assert sum(counts) == 4
    assert not worker.isRunning()

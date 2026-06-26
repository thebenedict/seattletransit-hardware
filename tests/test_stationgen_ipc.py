import contextlib
import io
import unittest

from stationgen.ipc import StationGenIPC


class FakeApiError(Exception):
    pass


class BusyRetryTests(unittest.TestCase):
    def make_generator(self):
        generator = StationGenIPC.__new__(StationGenIPC)
        generator.k = {"ApiError": FakeApiError}
        return generator

    def test_retries_kicad_busy_error(self):
        generator = self.make_generator()
        attempts = 0

        def callback():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise FakeApiError(
                    "KiCad returned error: KiCad is busy and cannot respond to API requests right now"
                )
            return "ok"

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = generator._retry_kicad_busy(
                "testing retry",
                callback,
                timeout_seconds=1.0,
                poll_interval_seconds=0.0,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)
        self.assertIn("KiCad is busy while testing retry", stderr.getvalue())

    def test_non_busy_api_error_is_not_retried(self):
        generator = self.make_generator()
        attempts = 0

        def callback():
            nonlocal attempts
            attempts += 1
            raise FakeApiError("different KiCad API error")

        with self.assertRaises(FakeApiError):
            generator._retry_kicad_busy("testing retry", callback, timeout_seconds=1.0)
        self.assertEqual(attempts, 1)

    def test_busy_timeout_uses_clear_error(self):
        generator = self.make_generator()

        def callback():
            raise FakeApiError("KiCad is busy and cannot respond to API requests right now")

        with self.assertRaisesRegex(RuntimeError, "KiCad stayed busy while testing retry"):
            generator._retry_kicad_busy("testing retry", callback, timeout_seconds=0.0)


if __name__ == "__main__":
    unittest.main()

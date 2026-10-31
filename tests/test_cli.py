import unittest
from unittest.mock import patch

# Keep local credentials out of tests.
with patch("dotenv.load_dotenv"):
    from app.main import main, parse_args


class KeepAwakeCliTests(unittest.TestCase):
    def test_keep_awake_is_opt_in_for_every_action(self):
        with patch("sys.argv", ["main.py"]):
            self.assertFalse(getattr(parse_args(), "keep_awake", False))
        for action in ("fetch", "submit", "linear", "submit-linear", "run", "loop", "heartbeat"):
            with self.subTest(action=action), patch("sys.argv", ["main.py", action, "--keep-awake"]):
                self.assertTrue(parse_args().keep_awake)

    def test_request_covers_async_runner_and_is_released_on_exit(self):
        for failure in (None, KeyboardInterrupt(), SystemExit(1), RuntimeError("command failed")):
            with self.subTest(failure=failure):
                events = []

                def execution_state(flags):
                    events.append(flags)
                    return 0x80000000

                def run(coro):
                    coro.close()
                    self.assertEqual(events, [0x80000001])
                    if failure is not None:
                        raise failure

                with patch("sys.argv", ["main.py", "loop", "--keep-awake"]), \
                     patch("app.power.sys.platform", "win32"), \
                     patch("app.power.ctypes.WinDLL", create=True) as dll, \
                     patch("app.main.asyncio.run", side_effect=run), \
                     patch("builtins.print"):
                    dll.return_value.SetThreadExecutionState.side_effect = execution_state
                    if isinstance(failure, (SystemExit, RuntimeError)):
                        with self.assertRaises(type(failure)):
                            main()
                    else:
                        main()
                self.assertEqual(events, [0x80000001, 0x80000000])


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import call, patch

from app.power import keep_awake


class KeepAwakeTests(unittest.TestCase):
    def test_disabled_does_not_load_windows_api(self):
        with patch("app.power.ctypes.WinDLL", create=True) as dll:
            with keep_awake(False):
                pass
            dll.assert_not_called()

    def test_other_platforms_continue_with_notice(self):
        with patch("app.power.sys.platform", "linux"), \
             patch("app.power.ctypes.WinDLL", create=True) as dll, \
             patch("builtins.print") as output:
            with keep_awake(True):
                pass
            dll.assert_not_called()
            self.assertIn("Windows", output.call_args.args[0])

    def test_restores_previous_thread_requirements(self):
        with patch("app.power.sys.platform", "win32"), \
             patch("app.power.ctypes.WinDLL", create=True) as dll, \
             patch("builtins.print"):
            api = dll.return_value.SetThreadExecutionState
            api.side_effect = [0x80000002, 0x80000001]
            with keep_awake(True):
                pass
            self.assertEqual(api.call_args_list, [call(0x80000001), call(0x80000002)])

    def test_rejected_request_does_not_start_work(self):
        with patch("app.power.sys.platform", "win32"), \
             patch("app.power.ctypes.WinDLL", create=True) as dll:
            api = dll.return_value.SetThreadExecutionState
            api.return_value = 0
            with self.assertRaisesRegex(RuntimeError, "keep-awake"):
                with keep_awake(True):
                    self.fail("Work started without sleep prevention")
            api.assert_called_once_with(0x80000001)

    def test_cleanup_failure_does_not_mask_command_error(self):
        with patch("app.power.sys.platform", "win32"), \
             patch("app.power.ctypes.WinDLL", create=True) as dll, \
             patch("builtins.print") as output:
            dll.return_value.SetThreadExecutionState.side_effect = [0x80000000, 0]
            with self.assertRaisesRegex(ValueError, "command failed"):
                with keep_awake(True):
                    raise ValueError("command failed")
            self.assertIn("restore", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

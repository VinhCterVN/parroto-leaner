import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Keep local credentials out of tests.
with patch("dotenv.load_dotenv"):
    from app.main import async_main, config, main, parse_args
    from app.config import Config


class KeepAwakeCliTests(unittest.TestCase):
    def test_keep_awake_is_opt_in_for_every_action(self):
        with patch("sys.argv", ["main.py"]):
            self.assertFalse(getattr(parse_args(), "keep_awake", False))
        for action in ("fetch", "submit", "linear", "submit-linear", "run", "loop", "group-loop", "heartbeat"):
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


class GroupLoopCliTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "decks.json"
        self.source.write_text(json.dumps([
            {"deckId": deck_id, "groups": [{"groupId": "g", "cards": ["c"]}]}
            for deck_id in ("env-deck", "cli-deck")
        ]), encoding="utf-8")

    def args(self, *flags):
        with patch("sys.argv", ["main.py", "group-loop", "--groups-file", str(self.source), *flags]):
            return parse_args()

    async def test_deck_precedence_and_existing_rating_settings(self):
        for flags, env_deck, expected in (
            ([], "", ["env-deck", "cli-deck"]),
            ([], "env-deck", ["env-deck"]),
            (["--deckId", "cli-deck", "--rating", "easy"], "env-deck", ["cli-deck"]),
            (["--deck-id", "cli-deck"], "env-deck", ["cli-deck"]),
        ):
            with self.subTest(flags=flags, env_deck=env_deck):
                manager = AsyncMock()
                manager.access_token = "test-token"
                manager.can_refresh = False
                with patch.object(config, "deck_id", env_deck), \
                     patch.object(config, "rating", "good"), \
                     patch.object(config, "heartbeat_enabled", False), \
                     patch("app.main.TokenManager", return_value=manager), \
                     patch("app.main.CardScraper") as scraper, \
                     patch("app.main.run_group_loop", new_callable=AsyncMock) as run:
                    await async_main(self.args(*flags, "--rounds", "1"))
                scraper.assert_not_called()
                decks, passed_manager, submitter = run.call_args.args
                self.assertEqual([deck.deck_id for deck in decks], expected)
                self.assertIs(passed_manager, manager)
                self.assertEqual(submitter.rating, "easy" if "--rating" in flags else "good")
                self.assertEqual(run.call_args.kwargs["rounds"], 1)
                self.assertEqual(run.call_args.kwargs["interval_minutes"], 0)

    async def test_no_valid_data_halts_before_authentication(self):
        self.source.write_text("[]", encoding="utf-8")
        with patch("app.main.TokenManager") as manager, patch("builtins.print") as output:
            with self.assertRaises(SystemExit) as error:
                await async_main(self.args())
        self.assertEqual(error.exception.code, 1)
        manager.assert_not_called()
        self.assertIn("No valid deck", output.call_args.args[0])

    async def test_failure_stops_heartbeat_and_exits_nonzero(self):
        from app.groups import GroupLoopError
        manager = AsyncMock()
        manager.access_token = "test-token"
        heartbeat = AsyncMock()
        with patch.object(config, "deck_id", ""), \
             patch("app.main.TokenManager", return_value=manager), \
             patch("app.main.HeartbeatService", return_value=heartbeat), \
             patch("app.main.run_group_loop", new_callable=AsyncMock, side_effect=GroupLoopError("reset failed")), \
             patch("builtins.print"):
            with self.assertRaises(SystemExit) as error:
                await async_main(self.args("--heartbeat"))
        self.assertEqual(error.exception.code, 1)
        heartbeat.start.assert_awaited_once()
        heartbeat.stop.assert_awaited_once()

    async def test_invalid_group_options_halt_before_authentication(self):
        for flags in (("--rounds", "0"), ("--concurrency", "0"), ("--interval", "-1"), ("--delay", "nan")):
            with self.subTest(flags=flags), patch.object(config, "deck_id", ""), \
                 patch("app.main.TokenManager") as manager, patch("builtins.print"):
                with self.assertRaises(SystemExit) as error:
                    await async_main(self.args(*flags))
                self.assertEqual(error.exception.code, 1)
                manager.assert_not_called()


class DeckEnvironmentTests(unittest.TestCase):
    def test_canonical_and_camel_case_environment_keys(self):
        for env, expected in (({}, ""), ({"deckId": "camel"}, "camel"), ({"DECK_ID": "canonical", "deckId": "camel"}, "canonical")):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                self.assertEqual(Config().deck_id, expected)


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

# Do not load the user's credentials in offline tests.
with patch("dotenv.load_dotenv"):
    from app.groups import GroupLoopError, GroupProgressClient, load_group_decks, run_group_loop
    from app.submitter import CardSubmitter


def deck(deck_id="deck-1", group_id="group-1", cards=None):
    return {
        "deckId": deck_id,
        "groups": [{"groupId": group_id, "cards": cards or ["card-1", "card-2"]}],
    }


class GroupDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, filename, data):
        path = self.root / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_reads_multiple_files_and_selects_exact_deck(self):
        self.write("first.json", [deck()])
        self.write("second.json", [deck("deck-2", "group-2", ["card-3"])])
        self.write("decks.example.json", [deck("example")])
        decks = load_group_decks(self.root)
        self.assertEqual([d.deck_id for d in decks], ["deck-1", "deck-2"])
        selected = load_group_decks(self.root, "deck-2")
        self.assertEqual([d.deck_id for d in selected], ["deck-2"])
        self.assertEqual(selected[0].groups[0].group_id, "group-2")
        self.assertEqual(selected[0].groups[0].cards[0].card_id, "card-3")

    def test_empty_missing_or_example_only_directory_fails(self):
        for source in (self.root, self.root / "missing.json"):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "No valid deck"):
                load_group_decks(source)
        self.write("decks.example.json", [deck()])
        with self.assertRaisesRegex(ValueError, "No valid deck"):
            load_group_decks(self.root)

    def test_missing_selected_deck_does_not_fall_back_to_all(self):
        path = self.write("decks.json", [deck()])
        with self.assertRaisesRegex(ValueError, "deck-missing"):
            load_group_decks(path, "deck-missing")

    def test_rejects_invalid_data_before_any_request(self):
        bad_payloads = [
            {}, [], [None], [{"deckId": "", "groups": []}],
            [{"deckId": "deck-1", "groups": []}],
            [{"deckId": "deck-1", "groups": [{"groupId": "", "cards": ["c"]}]}],
            [{"deckId": "deck-1", "groups": [{"groupId": "g", "cards": []}]}],
            [deck(cards=[""])], [deck(cards=[123])], [deck(cards=["c", "c"])],
            [deck(), deck()],
            [{"deckId": "deck-1", "groups": [deck()["groups"][0]] * 2}],
        ]
        for data in bad_payloads:
            with self.subTest(data=data):
                path = self.write("decks.json", data)
                with self.assertRaises(ValueError):
                    load_group_decks(path)

    def test_malformed_json_includes_filename_in_error(self):
        path = self.root / "broken.json"
        path.write_text("[{", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "broken.json"):
            load_group_decks(path)


class GroupNetworkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.token_manager = AsyncMock()
        self.token_manager.get_token.return_value = "initial-token"
        self.token_manager.can_refresh = True
        self.progress = GroupProgressClient(self.token_manager, cooldown_429=0)

    async def test_supported_progress_envelopes_remember_all_ids(self):
        groups = [{"id": "one"}, {"groupId": "two"}, {"_id": "three"}, {"group_id": "four"}]
        for data in (groups, {"groups": groups}, {"data": groups}, {"data": {"groups": groups}}):
            with self.subTest(data=data):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))) as client:
                    self.assertEqual(await self.progress.get_group_ids(client, "deck-1"), ["one", "two", "three", "four"])

    async def test_bearer_auth_and_one_refresh_on_401_for_get_and_delete(self):
        for method in ("GET", "DELETE"):
            with self.subTest(method=method):
                seen = []
                self.token_manager.reset_mock()
                self.token_manager.get_token.side_effect = ["old-token", "new-token"]

                def respond(request):
                    seen.append(request)
                    if len(seen) == 1:
                        return httpx.Response(401)
                    return httpx.Response(200, json={"groups": [{"id": "group-1"}]}) if method == "GET" else httpx.Response(204)

                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    if method == "GET":
                        await self.progress.get_group_ids(client, "deck-1")
                    else:
                        await self.progress.reset_group(client, "deck-1", "group-1")
                self.assertEqual([r.method for r in seen], [method, method])
                self.assertEqual([r.headers["Authorization"] for r in seen], ["Bearer old-token", "Bearer new-token"])
                expected_path = "/api/learning-vocabulary/groups-progress/deck-1" if method == "GET" else "/api/learning-vocabulary/group/deck-1/group-1"
                self.assertEqual(seen[0].url.path, expected_path)
                self.token_manager.refresh.assert_awaited_once()

    async def test_429_retries_are_bounded(self):
        attempts = []

        def respond(request):
            attempts.append(request)
            return httpx.Response(429)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaisesRegex(GroupLoopError, "429"):
                await self.progress.reset_group(client, "deck-1", "group-1")
        self.assertEqual(len(attempts), 4)

    async def test_bad_progress_or_api_error_fails(self):
        payloads = [{}, {"groups": []}, {"groups": [{}]}, {"groups": [{"id": 7}]}, {"status": "error", "groups": [{"id": "g"}]}]
        for data in payloads:
            with self.subTest(data=data):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))) as client:
                    with self.assertRaises(GroupLoopError):
                        await self.progress.get_group_ids(client, "deck-1")

    async def test_reset_rejects_http_and_application_failures(self):
        for status, data in ((500, {}), (401, {}), (200, {"status": "error"}), (200, {"success": False}), (200, {"error": "failed"}), (200, False), (200, [])):
            with self.subTest(status=status, data=data):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=data))) as client:
                    with self.assertRaises(GroupLoopError):
                        await self.progress.reset_group(client, "deck-1", "group-1")

    async def test_invalid_json_and_network_errors_stop(self):
        def malformed(request):
            return httpx.Response(200, text="not JSON")

        def offline(request):
            raise httpx.ConnectError("offline", request=request)

        for handler in (malformed, offline):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(GroupLoopError):
                    await self.progress.get_group_ids(client, "deck-1")

    async def test_refresh_failure_is_reported_without_exposing_credentials(self):
        self.token_manager.refresh.side_effect = RuntimeError("secret-token-value")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client:
            with self.assertRaises(GroupLoopError) as error:
                await self.progress.reset_group(client, "deck-1", "group-1")
        self.assertNotIn("secret-token-value", str(error.exception))


class GroupLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "decks.json"
        path.write_text(json.dumps([deck()]), encoding="utf-8")
        self.decks = load_group_decks(path)
        self.token_manager = AsyncMock()
        self.token_manager.get_token.return_value = "test-token"
        self.token_manager.can_refresh = False
        self.submitter = CardSubmitter(token_manager=self.token_manager, rating="easy", concurrency=1)
        self.real_client = httpx.AsyncClient

    def client_factory(self, handler):
        return lambda *args, **kwargs: self.real_client(transport=httpx.MockTransport(handler), timeout=20.0)

    async def test_resets_all_remote_groups_before_first_and_every_later_round(self):
        events = []

        def respond(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            events.append((request.method, request.url.path))
            if request.method == "GET":
                return httpx.Response(200, json={"data": {"groups": [{"id": "group-1"}, {"id": "group-2"}]}})
            if request.method == "DELETE":
                return httpx.Response(204)
            body = json.loads(request.content)
            self.assertEqual(body["rating"], "easy")
            return httpx.Response(200, json={"status": "success", "data": {}})

        with patch("app.groups.httpx.AsyncClient", side_effect=self.client_factory(respond)), patch("builtins.print"):
            await run_group_loop(self.decks, self.token_manager, self.submitter, rounds=2)
        self.assertEqual([method for method, _ in events], ["GET", "DELETE", "DELETE", "POST", "POST"] * 2)
        self.assertEqual(events[1][1], "/api/learning-vocabulary/group/deck-1/group-1")
        self.assertEqual(events[2][1], "/api/learning-vocabulary/group/deck-1/group-2")

    async def test_failed_reset_stops_without_submitting(self):
        events = []

        def respond(request):
            events.append(request.method)
            if request.method == "GET":
                return httpx.Response(200, json={"groups": [{"id": "group-1"}]})
            return httpx.Response(503)

        with patch("app.groups.httpx.AsyncClient", side_effect=self.client_factory(respond)), patch("builtins.print"):
            with self.assertRaisesRegex(GroupLoopError, "503"):
                await run_group_loop(self.decks, self.token_manager, self.submitter, rounds=2)
        self.assertEqual(events, ["GET", "DELETE"])

    async def test_failed_or_delayed_submit_stops_before_next_reset(self):
        for status, body in ((500, {"message": "failed"}), (200, {"message": "chưa thể review"})):
            with self.subTest(status=status):
                events = []

                def respond(request):
                    events.append(request.method)
                    if request.method == "GET":
                        return httpx.Response(200, json={"groups": [{"id": "group-1"}]})
                    if request.method == "DELETE":
                        return httpx.Response(204)
                    return httpx.Response(status, json=body)

                with patch("app.groups.httpx.AsyncClient", side_effect=self.client_factory(respond)), patch("builtins.print"):
                    with self.assertRaisesRegex(GroupLoopError, "submission"):
                        await run_group_loop(self.decks, self.token_manager, self.submitter, rounds=2)
                self.assertEqual(events, ["GET", "DELETE", "POST", "POST"])

    async def test_unknown_local_group_prevents_any_reset(self):
        methods = []

        def respond(request):
            methods.append(request.method)
            return httpx.Response(200, json={"groups": [{"id": "different-group"}]})

        with patch("app.groups.httpx.AsyncClient", side_effect=self.client_factory(respond)):
            with self.assertRaisesRegex(GroupLoopError, "group-1"):
                await run_group_loop(self.decks, self.token_manager, self.submitter, rounds=1)
        self.assertEqual(methods, ["GET"])

    async def test_linear_mode_preserves_pacing_and_rating(self):
        self.submitter.submit_linear = AsyncMock(return_value=[])
        self.submitter.submit_all = AsyncMock()

        def respond(request):
            return httpx.Response(200, json={"groups": [{"id": "group-1"}]}) if request.method == "GET" else httpx.Response(204)

        with patch("app.groups.httpx.AsyncClient", side_effect=self.client_factory(respond)), patch("builtins.print"):
            with self.assertRaisesRegex(GroupLoopError, "submission"):
                await run_group_loop(self.decks, self.token_manager, self.submitter, linear=True, delay=0.25, cooldown_429=3, rounds=1)
        self.submitter.submit_all.assert_not_awaited()
        self.submitter.submit_linear.assert_awaited_once()
        self.assertEqual(self.submitter.submit_linear.call_args.kwargs, {"delay": 0.25, "rate_limit_delay": 3})

    async def test_cancellation_propagates_without_further_resets(self):
        self.submitter.submit_all = AsyncMock(side_effect=asyncio.CancelledError())
        methods = []

        def respond(request):
            methods.append(request.method)
            return httpx.Response(200, json={"groups": [{"id": "group-1"}]}) if request.method == "GET" else httpx.Response(204)

        with patch("app.groups.httpx.AsyncClient", side_effect=self.client_factory(respond)), patch("builtins.print"):
            with self.assertRaises(asyncio.CancelledError):
                await run_group_loop(self.decks, self.token_manager, self.submitter)
        self.assertEqual(methods, ["GET", "DELETE"])

    async def test_invalid_options_halt_before_network_calls(self):
        for options in ({"rounds": 0}, {"rounds": -1}, {"interval_minutes": -1}, {"delay": float("nan")}, {"cooldown_429": float("inf")}):
            with self.subTest(options=options), patch("app.groups.httpx.AsyncClient") as client:
                with self.assertRaises(ValueError):
                    await run_group_loop(self.decks, self.token_manager, self.submitter, **options)
                client.assert_not_called()

    async def test_all_decks_are_checked_and_reset_before_any_submissions(self):
        path = Path(self.temp.name) / "two.json"
        path.write_text(json.dumps([deck(), deck("deck-2", "group-2", ["card-3"])]), encoding="utf-8")
        decks = load_group_decks(path)
        methods = []

        def respond(request):
            methods.append(request.method)
            if request.method == "GET":
                group_id = "group-1" if request.url.path.endswith("deck-1") else "group-2"
                return httpx.Response(200, json={"groups": [{"id": group_id}]})
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json={"status": "success", "data": {}})

        with patch("app.groups.httpx.AsyncClient", side_effect=self.client_factory(respond)), patch("builtins.print"):
            await run_group_loop(decks, self.token_manager, self.submitter, rounds=1)
        self.assertEqual(methods, ["GET", "GET", "DELETE", "DELETE", "POST", "POST", "POST"])

    async def test_invalid_second_deck_prevents_resets_of_first_deck(self):
        path = Path(self.temp.name) / "two.json"
        path.write_text(json.dumps([deck(), deck("deck-2", "group-2", ["card-3"])]), encoding="utf-8")
        methods = []

        def respond(request):
            methods.append(request.method)
            groups = [{"id": "group-1"}] if request.url.path.endswith("deck-1") else []
            return httpx.Response(200, json={"groups": groups})

        with patch("app.groups.httpx.AsyncClient", side_effect=self.client_factory(respond)), patch("builtins.print"):
            with self.assertRaises(GroupLoopError):
                await run_group_loop(load_group_decks(path), self.token_manager, self.submitter, rounds=1)
        self.assertEqual(methods, ["GET", "GET"])

    async def test_linear_submission_round_completes_after_reset(self):
        methods = []
        card_ids = []

        def respond(request):
            methods.append(request.method)
            if request.method == "GET":
                return httpx.Response(200, json={"groups": [{"id": "group-1"}]})
            if request.method == "DELETE":
                return httpx.Response(200, json={"status": "success"})
            payload = json.loads(request.content)
            card_ids.append(payload["cardId"])
            self.assertEqual(payload["rating"], "easy")
            return httpx.Response(200, json={"status": "success", "data": {}})

        with patch("app.groups.httpx.AsyncClient", side_effect=self.client_factory(respond)), patch("builtins.print"):
            await run_group_loop(self.decks, self.token_manager, self.submitter, linear=True, delay=0, rounds=1)
        self.assertEqual(methods, ["GET", "DELETE", "POST", "POST"])
        self.assertEqual(card_ids, ["card-1", "card-2"])


if __name__ == "__main__":
    unittest.main()

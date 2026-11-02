import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

import requests

import index as app


def response(body=None, text="", status=200):
    result = Mock(status_code=status, text=text)
    result.json.return_value = body
    return result


class ResultReasonTests(unittest.TestCase):
    def setUp(self):
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        network = patch.object(
            requests.sessions.Session,
            "request",
            side_effect=AssertionError("offline test attempted network"),
        )
        network.start()
        self.addCleanup(network.stop)

    def run_account(self, captcha=None, login=None, sign=None):
        session = Mock()
        page = response(
            text='name="captchaToken" value="token"; lt="lt"; '
            'paramId="param"; reqId="req";'
        )
        session.get.side_effect = [
            page,
            sign
            if sign is not None
            else response({"isSign": False, "netdiskBonus": 63}),
        ]
        session.post.side_effect = [
            response({"result": 0, "data": {"pubKey": "key", "pre": "prefix"}}),
            captcha if captcha is not None else response(text="0"),
            login
            if login is not None
            else response({"result": 0, "toUrl": "https://example.invalid/"}),
            response({"res_code": 0, "sessionKey": "fake-session"}),
        ]
        env = {
            "TY_ACCOUNTS": json.dumps(
                [{"username": "offline-account", "password": "offline-password"}]
            )
        }
        with patch.dict(app.os.environ, env, clear=True), patch.object(
            app.requests, "Session", return_value=session
        ), patch.object(app, "rsa_encode", return_value="encrypted"):
            body = json.loads(app.main_handler({}, None)["body"])
        session.close.assert_called_once()
        self.assertEqual(body["notification"], "disabled")
        return body, session

    def assert_failure(self, body, reason):
        self.assertFalse(body["ok"])
        self.assertEqual(
            body["counts"], {"signed": 0, "already_signed": 0, "failed": 1}
        )
        self.assertEqual(body["results"][0]["status"], "failed")
        self.assertEqual(body["results"][0]["reason_code"], reason)

    def test_captcha_requirement_is_not_http_or_protocol_failure(self):
        for status, text, reason in (
            (200, "1", "captcha_required"),
            (503, "1", "unknown"),
            (200, "<html>captcha required</html>", "unknown"),
            (200, "2", "unknown"),
            (200, "", "unknown"),
        ):
            with self.subTest(status=status, text=text):
                body, session = self.run_account(
                    captcha=response(text=text, status=status)
                )
                self.assert_failure(body, reason)
                self.assertEqual(session.post.call_count, 2)
                self.assertEqual(session.get.call_count, 1)

    def test_device_challenge_stops_before_session_or_sign_requests(self):
        for code in (-133, "-133"):
            with self.subTest(code=code):
                body, session = self.run_account(
                    login=response({"result": code, "msg": "arbitrary text"})
                )
                self.assert_failure(body, "device_verification_required")
                self.assertEqual(session.post.call_count, 3)
                self.assertEqual(session.get.call_count, 1)
        body, _ = self.run_account(login=response({"result": -133}, status=500))
        self.assert_failure(body, "unknown")

    def test_login_rejection_does_not_guess_authentication_from_message(self):
        body, session = self.run_account(
            login=response({"result": -9, "msg": "需要验证码"})
        )
        self.assert_failure(body, "login_rejected")
        self.assertEqual(session.post.call_count, 3)
        self.assertEqual(session.get.call_count, 1)

    def test_malformed_login_result_is_unknown(self):
        for code in (None, False, True, 0.0, [], {}, "bad", 0):
            with self.subTest(code=code):
                body, session = self.run_account(login=response({"result": code}))
                self.assert_failure(body, "unknown")
                self.assertEqual(session.post.call_count, 3)
                self.assertEqual(session.get.call_count, 1)

    def test_explicit_sign_rejection_and_uncertain_results_remain_distinct(self):
        for payload, reason in (
            ({"errorCode": "InvalidSessionKey"}, "sign_rejected"),
            ({"errorCode": -1}, "sign_rejected"),
            ({}, "unknown"),
            (None, "unknown"),
            ({"isSign": 1, "netdiskBonus": 63}, "unknown"),
        ):
            with self.subTest(payload=payload):
                body, session = self.run_account(sign=response(payload))
                self.assert_failure(body, reason)
                self.assertEqual(session.get.call_count, 2)
        body, session = self.run_account(
            sign=requests.Timeout("https://example.invalid/?secret=FAKE_SECRET")
        )
        self.assert_failure(body, "unknown")
        self.assertNotIn("FAKE_SECRET", json.dumps(body))
        self.assertEqual(session.get.call_count, 2)

    def test_success_reasons_preserve_legacy_status_and_counts(self):
        for state, status in ((False, "signed"), (True, "already_signed")):
            with self.subTest(state=state):
                body, session = self.run_account(
                    sign=response({"isSign": state, "netdiskBonus": 63})
                )
                self.assertTrue(body["ok"])
                counts = {"signed": 0, "already_signed": 0, "failed": 0}
                counts[status] = 1
                self.assertEqual(body["counts"], counts)
                self.assertEqual(body["results"][0]["status"], status)
                self.assertEqual(body["results"][0]["reason_code"], status)
                self.assertEqual(session.get.call_count, 2)

    def test_config_error_has_machine_reason_and_no_session(self):
        with patch.dict(
            app.os.environ, {"TY_ACCOUNTS": "bad"}, clear=True
        ), patch.object(app.requests, "Session") as session:
            body = json.loads(app.main_handler({}, None)["body"])
        self.assertEqual(body["reason_code"], "invalid_configuration")
        self.assertEqual(body["notification"], "not_attempted")
        self.assertFalse(body["ok"])
        session.assert_not_called()


if __name__ == "__main__":
    unittest.main()

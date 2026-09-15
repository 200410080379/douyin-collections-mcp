"""Browser transport regressions without opening a browser or making HTTP calls."""

import json
from collections import deque
from copy import deepcopy

import pytest
from playwright.async_api import Error as PlaywrightError

from douyin_collections_mcp.browser import (
    COLLECT,
    FAVORITES,
    FOLDERS,
    MAINTAIN_FOLDER,
    MOVE_VIDEO,
    ORIGIN,
    SELF,
    BrowserClient,
)
from douyin_collections_mcp.config import Settings
from douyin_collections_mcp.errors import DouyinError

BIG_ID = "1234567890123456789"
SECOND_ID = "1234567890123456790"
ACCOUNT_ID = "7311111111111111111"
SECRET_MARKER = "test-only-session-token-do-not-expose"
WRITE_DEFAULTS = {
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "version_code": "170400",
    "version_name": "17.4.0",
    "aid": "6383",
    "device_platform": "webapp",
}


def response(payload=None, *, status=200, text=None):
    return {"status": status, "text": text if text is not None else json.dumps(payload)}


def account_response(account_id=ACCOUNT_ID):
    return response(
        {
            "status_code": 0,
            "user": {"uid": account_id, "sec_uid": "my-sec-uid", "nickname": "本人"},
        }
    )


class FakePage:
    """Return the actual wire text shape expected from the browser bridge.

    Deliberately do not JSON-parse the response inside this fake; doing so would
    hide the JavaScript 64-bit precision regression that this suite targets.
    """

    url = f"{ORIGIN}/user/self"

    def __init__(self, owner):
        self.owner = owner
        self.responses = deque()
        self.calls = []
        self.wait_calls = []
        self.wait_error = None

    def is_closed(self):
        return False

    async def wait_for_function(self, expression, *, timeout):
        self.wait_calls.append({"expression": expression, "timeout": timeout})
        if self.wait_error:
            raise self.wait_error

    async def evaluate(self, script, arguments):
        self.calls.append({"script": script, "arguments": deepcopy(arguments)})
        self.owner._last_request = 0.0  # No real rate-limit waiting is needed for a fake.
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def browser(tmp_path):
    client = BrowserClient(Settings(tmp_path / "transport-data"))
    client.context = object()  # Satisfies start's existing-session branch.
    client.page = FakePage(client)
    return client


async def test_raw_json_numeric_folder_id_is_parsed_in_python_without_rounding(browser):
    # A JSON number, not a quoted string: this previously rounded in response.json().
    wire_text = (
        '{"status_code":0,"collects_list":[{"collects_id":1234567890123456789,'
        '"collects_name":"学习","total_number":1,"status":0}],"has_more":0}'
    )
    browser.page.responses.append(response(text=wire_text))
    result = await browser.list_folders()
    assert result["items"][0]["id"] == BIG_ID
    assert result["items"][0]["visibility"] == "private"
    assert browser.page.calls[0]["arguments"]["useSiteClient"] is False
    assert browser.page.wait_calls == []
    bridge_script = browser.page.calls[0]["script"]
    # Assert the wire boundary as well as the Python result; the fake must not
    # accidentally allow a future return to JavaScript JSON parsing.
    assert "response.text()" in bridge_script
    assert "response.json()" not in bridge_script


async def test_two_folder_reads_do_not_replay_observed_token_or_signature_params(browser):
    browser._templates[FOLDERS] = {
        "method": "GET",
        "params": {
            "msToken": SECRET_MARKER,
            "a_bogus": "stale-a-bogus",
            "X-Bogus": "stale-x-bogus",
            "signature": "stale-signature",
            "verifyFp": "old-verification",
            "version_name": "old-version",
            "cursor": "old-cursor",
            "aid": "old-aid",
        },
        "body": {},
    }
    browser.page.responses.extend(
        [
            response({"status_code": 0, "collects_list": [], "has_more": 1, "cursor": "20"}),
            response({"status_code": 0, "collects_list": [], "has_more": 0}),
        ]
    )
    await browser.list_folders(cursor="0", limit=10)
    await browser.list_folders(cursor="20", limit=10)
    for call, cursor in zip(browser.page.calls, ["0", "20"], strict=True):
        arguments = call["arguments"]
        assert arguments["query"] == {
            "aid": "6383",
            "device_platform": "webapp",
            "cursor": cursor,
            "count": "10",
        }
        assert arguments["method"] == "GET"
        assert arguments["body"] is None
        assert arguments["useSiteClient"] is False
        assert SECRET_MARKER not in json.dumps(arguments)
    assert browser.page.wait_calls == []


async def test_favorite_read_keeps_pagination_in_post_form_body(browser):
    browser._templates[FAVORITES] = {
        "method": "POST",
        "params": {"msToken": SECRET_MARKER},
        "body": {"cursor": "stale-cursor", "session_token": SECRET_MARKER},
    }
    browser.page.responses.append(response({"status_code": 0, "aweme_list": [], "has_more": 0}))
    await browser.list_videos("favorites", cursor="1750000000000001", limit=10)
    call = browser.page.calls[0]
    assert call["arguments"] == {
        "path": FAVORITES,
        "query": {"aid": "6383", "device_platform": "webapp"},
        "method": "POST",
        "body": {"cursor": "1750000000000001", "count": "10"},
        "useSiteClient": False,
    }
    assert browser.page.wait_calls == []
    assert "application/x-www-form-urlencoded" in call["script"]
    assert "new URLSearchParams(body).toString()" in call["script"]


@pytest.mark.parametrize(
    "path, business_params",
    [
        (MAINTAIN_FOLDER, {"action": "1", "collects_name": "学习", "secret": "1"}),
        (
            MOVE_VIDEO,
            {
                "item_ids": [BIG_ID, SECOND_ID],
                "item_type": "2",
                "to_collects_id": "7412345678912345678",
                "update_collects_sort": "true",
            },
        ),
    ],
)
async def test_folder_writes_are_post_with_business_parameters_in_query(
    browser,
    path,
    business_params,
):
    browser.page.responses.extend([account_response(), response({"status_code": 0})])
    await browser._write(path, business_params, ACCOUNT_ID)
    assert [call["arguments"]["path"] for call in browser.page.calls] == [SELF, path]
    sent = browser.page.calls[-1]["arguments"]
    assert sent["method"] == "POST"
    assert sent["body"] == {}
    assert sent["query"] == {**WRITE_DEFAULTS, **business_params}
    assert sent["useSiteClient"] is True
    assert browser.page.calls[0]["arguments"]["useSiteClient"] is False
    assert len(browser.page.wait_calls) == 1
    assert "axiosInstance.request" in browser.page.wait_calls[0]["expression"]
    if path == MOVE_VIDEO:
        assert sent["query"]["item_ids"] == [BIG_ID, SECOND_ID]
        assert all(isinstance(value, str) for value in sent["query"]["item_ids"])
        script = browser.page.calls[-1]["script"]
        assert "Array.isArray(v)" in script
        assert "search.append(k, String(item))" in script


async def test_favorite_write_sends_video_id_action_and_type_in_form_body(browser):
    browser.page.responses.extend([account_response(), response({"status_code": 0})])
    body = {"aweme_id": BIG_ID, "action": "1", "aweme_type": "68"}
    await browser._write(COLLECT, {}, ACCOUNT_ID, body=body)
    call = browser.page.calls[-1]
    assert call["arguments"] == {
        "path": COLLECT,
        "query": WRITE_DEFAULTS,
        "method": "POST",
        "body": body,
        "useSiteClient": True,
    }
    assert "window.axiosInstance.request" in call["script"]
    assert "application/x-www-form-urlencoded; charset=UTF-8" in call["script"]
    assert len(browser.page.wait_calls) == 1


async def test_site_client_write_keeps_numeric_response_ids_and_explicit_version(browser):
    browser.page.responses.extend(
        [
            account_response(),
            response(text='{"status_code":0,"collects_id":1234567890123456789}'),
        ]
    )
    result = await browser._write(
        MAINTAIN_FOLDER,
        {"action": "1", "version_code": "230000", "version_name": "23.0.0"},
        ACCOUNT_ID,
    )
    assert str(result["collects_id"]) == BIG_ID
    call = browser.page.calls[-1]
    assert call["arguments"]["useSiteClient"] is True
    assert call["arguments"]["query"]["version_code"] == "230000"
    assert call["arguments"]["query"]["version_name"] == "23.0.0"
    assert "responseType: 'text'" in call["script"]
    assert "transformResponse: [raw => raw]" in call["script"]
    assert "text: response.data" in call["script"]


async def test_missing_site_request_client_stops_write_without_exposing_wait_error(browser):
    browser.page.responses.append(account_response())
    browser.page.wait_error = PlaywrightError(f"client wait failed: sessionid={SECRET_MARKER}")
    with pytest.raises(DouyinError) as error:
        await browser._write(MOVE_VIDEO, {"item_ids": [BIG_ID]}, ACCOUNT_ID)
    assert error.value.code == "write_uncertain"
    assert SECRET_MARKER not in json.dumps(error.value.as_dict())
    assert [call["arguments"]["path"] for call in browser.page.calls] == [SELF]
    assert len(browser.page.wait_calls) == 1


@pytest.mark.parametrize("path", [MAINTAIN_FOLDER, MOVE_VIDEO, COLLECT])
async def test_expected_account_mismatch_never_evaluates_a_write_request(browser, path):
    browser.page.responses.append(account_response("7322222222222222222"))
    with pytest.raises(DouyinError) as error:
        await browser._write(path, {}, ACCOUNT_ID)
    assert error.value.code == "account_mismatch"
    assert [call["arguments"]["path"] for call in browser.page.calls] == [SELF]
    assert browser.page.wait_calls == []


@pytest.mark.parametrize(
    "wire_result, expected_code",
    [
        (response(status=403, text=SECRET_MARKER), "login_or_verification_required"),
        (response(status=429, text=SECRET_MARKER), "login_or_verification_required"),
        (response(text=f"<html>{SECRET_MARKER}</html>"), "unexpected_response"),
        (response({"status_code": 9999, "status_msg": SECRET_MARKER}), "platform_error"),
        (PlaywrightError(f"fetch failed: sessionid={SECRET_MARKER}"), "request_failed"),
    ],
)
async def test_read_errors_never_expose_raw_body_or_session_tokens(
    browser, wire_result, expected_code
):
    browser.page.responses.append(wire_result)
    with pytest.raises(DouyinError) as error:
        await browser._request(FOLDERS)
    assert error.value.code == expected_code
    assert SECRET_MARKER not in str(error.value)
    assert SECRET_MARKER not in json.dumps(error.value.as_dict())


@pytest.mark.parametrize(
    "wire_result",
    [
        response(text=f"<html>{SECRET_MARKER}</html>"),
        PlaywrightError(f"write timed out: msToken={SECRET_MARKER}"),
    ],
)
async def test_write_transport_failure_is_uncertain_and_does_not_leak_tokens(browser, wire_result):
    browser.page.responses.extend([account_response(), wire_result])
    with pytest.raises(DouyinError) as error:
        await browser._write(MOVE_VIDEO, {"item_ids": [BIG_ID]}, ACCOUNT_ID)
    assert error.value.code == "write_uncertain"
    assert SECRET_MARKER not in json.dumps(error.value.as_dict())
    assert [call["arguments"]["path"] for call in browser.page.calls] == [SELF, MOVE_VIDEO]


async def test_arbitrary_endpoint_is_rejected_before_page_evaluation(browser):
    with pytest.raises(DouyinError) as error:
        await browser._request("/passport/session/export/")
    assert error.value.code == "unsupported_endpoint"
    assert browser.page.calls == []

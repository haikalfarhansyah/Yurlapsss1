"""Uji pesan pembaruan: galat server tidak boleh ditampilkan mentah-mentah."""
import io
import unittest
import urllib.error

from app.updater import MSG_FAILED, MSG_NO_RELEASE, MSG_OFFLINE, _friendly_error


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://yurlaps.dpdns.org/releases/manifest.json",
                                   code, "Bad Request", {}, io.BytesIO(body))


class FriendlyErrorTest(unittest.TestCase):
    def test_missing_object_is_reported_as_no_release(self):
        body = (b'{"statusCode":"404","error":"not_found","message":"Object not found",'
                b'"code":"NoSuchKey"}')
        self.assertEqual(_friendly_error(http_error(400, body)), MSG_NO_RELEASE)

    def test_plain_404_is_reported_as_no_release(self):
        self.assertEqual(_friendly_error(http_error(404)), MSG_NO_RELEASE)

    def test_message_is_stable_when_called_twice(self):
        error = http_error(400, b'{"code":"NoSuchKey"}')
        self.assertEqual(_friendly_error(error), MSG_NO_RELEASE)
        self.assertEqual(_friendly_error(error), MSG_NO_RELEASE)

    def test_network_failure_is_reported_as_offline(self):
        self.assertEqual(_friendly_error(OSError("timed out")), MSG_OFFLINE)
        self.assertEqual(_friendly_error(OSError("Connection refused")), MSG_OFFLINE)

    def test_other_errors_use_the_generic_message(self):
        self.assertEqual(_friendly_error(ValueError("sesuatu")), MSG_FAILED)
        self.assertEqual(_friendly_error(http_error(500)), MSG_FAILED)

    def test_messages_never_contain_server_dumps(self):
        error = http_error(400, b'{"code":"NoSuchKey"}')
        message = _friendly_error(error)
        for word in ("method:", "Headers:", "statusCode", "sb-request-id", "{"):
            self.assertNotIn(word, message)


if __name__ == "__main__":
    unittest.main()

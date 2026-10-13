import base64

from qr_slip import issue_slip_qr_data_uri


def test_issue_slip_qr_data_uri_contains_svg():
    data_uri = issue_slip_qr_data_uri(
        "https://inventory.example/coordinator/issue/42/slip"
    )

    prefix, encoded_svg = data_uri.split(",", 1)
    svg = base64.b64decode(encoded_svg)

    assert prefix == "data:image/svg+xml;base64"
    assert b"<svg" in svg[:1000]
    assert b"<path" in svg

"""Generate self-contained QR codes for printable issue slips."""

import base64
from io import BytesIO

import qrcode
from qrcode.image.svg import SvgPathImage


def issue_slip_qr_data_uri(url: str) -> str:
    """Return a base64 SVG data URI encoding the supplied issue-slip URL."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    output = BytesIO()
    image.save(output)
    encoded_svg = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded_svg}"
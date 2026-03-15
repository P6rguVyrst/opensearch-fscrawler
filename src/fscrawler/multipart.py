# Licensed under the Apache License, Version 2.0
"""Minimal multipart/form-data parser using Python stdlib only.

Deliberately avoids third-party libraries (e.g. python-multipart) to
eliminate supply-chain risk.  Uses the email.parser module which ships
with every CPython distribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser


@dataclass
class FormFile:
    """A single file field extracted from a multipart/form-data body."""

    field_name: str
    filename: str
    data: bytes
    content_type: str


def parse_multipart(content_type_header: str, body: bytes) -> list[FormFile]:
    """Parse a multipart/form-data body and return all file fields.

    Parameters
    ----------
    content_type_header:
        The full value of the ``Content-Type`` request header, e.g.
        ``"multipart/form-data; boundary=----WebKitFormBoundary..."``
    body:
        Raw request body bytes.

    Returns
    -------
    list[FormFile]
        One entry per ``form-data`` part that carries a ``filename``
        parameter.  Non-file fields (plain text inputs) are ignored.

    Raises
    ------
    ValueError
        If the Content-Type is not multipart/form-data or has no boundary.
    """
    ct_lower = content_type_header.lower()
    if not ct_lower.startswith("multipart/form-data"):
        raise ValueError(
            f"Expected multipart/form-data, got: {content_type_header!r}"
        )
    if "boundary=" not in ct_lower:
        raise ValueError(
            f"multipart/form-data header is missing boundary: {content_type_header!r}"
        )

    # Construct a synthetic MIME message so the stdlib email parser can handle
    # it.  The email module expects headers separated from the body by a blank
    # line, so we prepend the Content-Type header.
    mime_bytes = (
        f"Content-Type: {content_type_header}\r\n\r\n".encode("latin-1") + body
    )
    msg = BytesParser().parsebytes(mime_bytes)

    files: list[FormFile] = []
    for part in msg.walk():
        disposition = part.get_content_disposition()
        if disposition != "form-data":
            continue

        # get_params returns list of (key, value) tuples; first is the
        # disposition value itself ("form-data"), remainder are params.
        params = dict(part.get_params(header="content-disposition")[1:])
        filename = params.get("filename")
        if filename is None:
            continue  # plain text field, not a file upload

        field_name = params.get("name", "file")
        raw = part.get_payload(decode=True)
        if not isinstance(raw, bytes):
            continue

        files.append(
            FormFile(
                field_name=field_name,
                filename=filename,
                data=raw,
                content_type=part.get_content_type() or "application/octet-stream",
            )
        )

    return files

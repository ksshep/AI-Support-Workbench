"""Test fixtures: generate valid TXT/PDF bytes offline.

The PDF is built by hand (a minimal PDF with the standard Helvetica font) so
tests never need a real document and never touch the network. Pages whose
text is empty exercise the "blank page is ignored" path.
"""

from __future__ import annotations


def escape_pdf(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf_bytes(pages: list[str]) -> bytes:
    """Build a minimal PDF where each entry is one page's text ('' = blank).

    Uses the standard 14 font (Helvetica) so ``pypdf`` can extract the text
    without any embedded font resources.
    """
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + 3 * i} 0 R" for i in range(len(pages)))
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    )
    for i, text in enumerate(pages):
        page_id = 3 + 3 * i
        content_id = page_id + 1
        font_id = page_id + 2
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode()
        )
        stream = ""
        if text:
            stream = f"BT /F1 12 Tf 72 720 Td ({escape_pdf(text)}) Tj ET"
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode()
        )
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects):
        offsets.append(len(out))
        out += f"{i + 1} 0 obj\n".encode()
        out += obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)


SAMPLE_TXT = (
    "AI 客服支持系统使用指南\n"
    "如何重置密码：在登录页点击忘记密码，输入注册邮箱，"
    "系统会发送重置链接。\n"
    "如何提交工单：登录后进入我的工单，点击新建工单，"
    "填写标题和问题描述。"
)


def sample_txt_bytes() -> bytes:
    return SAMPLE_TXT.encode("utf-8")


def sample_pdf_bytes() -> bytes:
    return make_pdf_bytes(
        [
            "How to reset password: click forgot password on login page.",
            "How to submit a ticket: create a new ticket from dashboard.",
            "",  # blank page — must be ignored
        ]
    )

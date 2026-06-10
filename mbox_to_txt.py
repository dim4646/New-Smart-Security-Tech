#!/usr/bin/env python3
"""
Convert .mbox email archives to .txt files.

Usage:
  python3 mbox_to_txt.py
  python3 mbox_to_txt.py --input "Supatech.mbox" "Dimos WorkCover - Archive.mbox" --output email_txt_exports

What it creates:
  email_txt_exports/
    Supatech/
      Supatech_all_emails.txt
      emails/
        000001_2024-01-15_subject.txt
        000002_2024-01-16_subject.txt
    Dimos_WorkCover_Archive/
      Dimos_WorkCover_Archive_all_emails.txt
      emails/
        000001_2024-02-01_subject.txt
    email_index.csv
    email_index.json
"""

import argparse
import csv
import hashlib
import json
import mailbox
import os
import re
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_INPUTS = [
    "Supatech.mbox",
    "Dimos WorkCover - Archive.mbox",
]

DEFAULT_OUTPUT_DIR = "email_txt_exports"


def decode_mime_header(value: Optional[str]) -> str:
    """Decode MIME encoded email headers safely."""
    if not value:
        return ""

    decoded = []
    for part, encoding in decode_header(value):
        if isinstance(part, bytes):
            decoded.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded.append(part)

    return "".join(decoded).strip()


def safe_filename(value: str, max_len: int = 80) -> str:
    """Create filesystem-safe filename text."""
    value = value.strip() or "no_subject"
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value[:max_len].strip("._ ") or "no_subject"


def parse_date(value: Optional[str]) -> Dict[str, str]:
    """Return ISO datetime and yyyy-mm-dd date string."""
    if not value:
        return {"iso": "", "date": "unknown_date"}

    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is not None:
            iso = dt.isoformat()
        else:
            iso = dt.replace(tzinfo=None).isoformat()
        return {"iso": iso, "date": dt.strftime("%Y-%m-%d")}
    except Exception:
        return {"iso": value, "date": "unknown_date"}


def decode_payload(part: Any) -> str:
    """Decode one email MIME part."""
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""

    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def html_to_text(html: str) -> str:
    """Basic HTML to text fallback without external libraries."""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_body(message: Any) -> Dict[str, str]:
    """Extract best available text and html body from a message."""
    text_parts: List[str] = []
    html_parts: List[str] = []

    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()

            if "attachment" in disposition:
                continue

            if content_type == "text/plain":
                text_parts.append(decode_payload(part))
            elif content_type == "text/html":
                html_parts.append(decode_payload(part))
    else:
        content_type = message.get_content_type()
        if content_type == "text/html":
            html_parts.append(decode_payload(message))
        else:
            text_parts.append(decode_payload(message))

    text = "\n\n".join(p.strip() for p in text_parts if p and p.strip()).strip()
    html = "\n\n".join(p.strip() for p in html_parts if p and p.strip()).strip()

    if not text and html:
        text = html_to_text(html)

    return {"text": text, "html": html}


def get_attachments(message: Any) -> List[str]:
    """Return attachment filenames only. Does not export attachments."""
    attachments = []
    for part in message.walk() if message.is_multipart() else []:
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = decode_mime_header(part.get_filename())
        if "attachment" in disposition or filename:
            if filename:
                attachments.append(filename)
    return attachments


def format_email_block(number: int, record: Dict[str, Any]) -> str:
    sep = "=" * 80
    small_sep = "-" * 80
    attachments = ", ".join(record["attachments"]) if record["attachments"] else ""

    return (
        f"{sep}\n"
        f"Email #{number}\n"
        f"Folder:      {record['source_label']}\n"
        f"Date:        {record['date']}\n"
        f"From:        {record['from']}\n"
        f"To:          {record['to']}\n"
        f"Cc:          {record['cc']}\n"
        f"Subject:     {record['subject']}\n"
        f"Message-ID:  {record['message_id']}\n"
        f"Attachments: {attachments}\n"
        f"{small_sep}\n"
        f"{record['body_text']}\n\n"
    )


def process_mbox(mbox_path: Path, output_dir: Path) -> List[Dict[str, Any]]:
    label = safe_filename(mbox_path.stem, max_len=60)
    label_dir = output_dir / label
    email_dir = label_dir / "emails"
    email_dir.mkdir(parents=True, exist_ok=True)

    combined_path = label_dir / f"{label}_all_emails.txt"
    records: List[Dict[str, Any]] = []

    print(f"\nProcessing: {mbox_path}")

    mbox = mailbox.mbox(str(mbox_path))

    with combined_path.open("w", encoding="utf-8") as combined:
        combined.write(f"EMAIL EXPORT FROM MBOX\n")
        combined.write(f"Source file: {mbox_path.name}\n")
        combined.write(f"Exported at: {datetime.now().isoformat(timespec='seconds')}\n")
        combined.write("=" * 80 + "\n\n")

        for index, message in enumerate(mbox, start=1):
            subject = decode_mime_header(message.get("Subject")) or "(no subject)"
            from_value = decode_mime_header(message.get("From"))
            to_value = decode_mime_header(message.get("To"))
            cc_value = decode_mime_header(message.get("Cc"))
            message_id = decode_mime_header(message.get("Message-ID"))
            date_info = parse_date(message.get("Date"))
            body = extract_body(message)
            attachments = get_attachments(message)

            body_text = body["text"].strip()
            if not body_text:
                body_text = "(no text body found)"

            hash_source = f"{message_id}|{date_info['iso']}|{subject}|{from_value}|{body_text[:200]}"
            email_hash = hashlib.sha256(hash_source.encode("utf-8", errors="replace")).hexdigest()[:16]

            filename = f"{index:06d}_{date_info['date']}_{safe_filename(subject, 60)}.txt"
            single_path = email_dir / filename

            record = {
                "source_mbox": str(mbox_path),
                "source_label": label,
                "email_number": index,
                "date": date_info["iso"],
                "date_only": date_info["date"],
                "from": from_value,
                "to": to_value,
                "cc": cc_value,
                "subject": subject,
                "message_id": message_id,
                "attachments": attachments,
                "body_text": body_text,
                "txt_file": str(single_path),
                "email_hash": email_hash,
            }

            text_block = format_email_block(index, record)

            with single_path.open("w", encoding="utf-8") as f:
                f.write(text_block)

            combined.write(text_block)

            csv_record = dict(record)
            csv_record.pop("body_text", None)
            csv_record["attachments"] = "; ".join(attachments)
            records.append(csv_record)

            if index % 100 == 0:
                print(f"  {index} emails exported...")

    print(f"  Done: {len(records)} emails")
    print(f"  Combined TXT: {combined_path}")
    print(f"  Individual TXT folder: {email_dir}")
    return records


def write_indexes(output_dir: Path, records: List[Dict[str, Any]]) -> None:
    csv_path = output_dir / "email_index.csv"
    json_path = output_dir / "email_index.json"

    fieldnames = [
        "source_mbox",
        "source_label",
        "email_number",
        "date",
        "date_only",
        "from",
        "to",
        "cc",
        "subject",
        "message_id",
        "attachments",
        "txt_file",
        "email_hash",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"\nIndex CSV:  {csv_path}")
    print(f"Index JSON: {json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert MBOX files to TXT files.")
    parser.add_argument(
        "--input",
        nargs="*",
        default=DEFAULT_INPUTS,
        help="MBOX file paths. Default: Supatech.mbox and Dimos WorkCover - Archive.mbox",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output folder. Default: {DEFAULT_OUTPUT_DIR}",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_records: List[Dict[str, Any]] = []

    for input_file in args.input:
        mbox_path = Path(input_file)
        if not mbox_path.exists():
            print(f"\nWARNING: File not found: {mbox_path}")
            continue
        if mbox_path.suffix.lower() != ".mbox":
            print(f"\nWARNING: Not an .mbox file, skipped: {mbox_path}")
            continue
        all_records.extend(process_mbox(mbox_path, output_dir))

    if all_records:
        write_indexes(output_dir, all_records)
        print(f"\nFinished. Total emails exported: {len(all_records)}")
        print(f"Upload the combined .txt files to NotebookLM, or use the individual files for detailed search.")
    else:
        print("\nNo emails exported. Check the file names and location of your .mbox files.")


if __name__ == "__main__":
    main()

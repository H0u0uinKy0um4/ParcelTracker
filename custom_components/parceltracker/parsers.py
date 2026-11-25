"""Pure email/tracking-number parsing helpers.

The integration never stores the raw subject or body of a forwarded message:
they routinely contain sender names, addresses and order IDs. Only the two
numbers the user asked for (``Číslo zásilky`` and ``Číslo balíku``) survive as
attributes; everything else here is local, transient parsing state.

No Home Assistant, aiohttp or voluptuous imports — this module is unit tested
standalone.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message
from html.parser import HTMLParser
import re
import unicodedata
from typing import Any, Callable, Iterable


@dataclass(slots=True)
class ParsedEmail:
    """The only data extracted from a forwarded message that we keep."""

    message_id: str | None = None
    tracking_code: str | None = None
    pickup_code: str | None = None
    carrier: str | None = None


# Labelled values. Regexes are case-insensitive on purpose — forwarded
# messages mix Czech diacritics, transliterated Czech and English labels.
TRACKING_LABELS: tuple[str, ...] = (
    r"číslo\s+zásilky",
    r"cislo\s+zasilky",
    r"číslo\s+balíku\s+k\s+podání",  # explicit "parcel number for posting"
    r"tracking\s*(?:number|no\.?|id)",
    r"parcel\s*(?:number|no\.?|id)",
    r"shipment\s*(?:number|no\.?|id)",
    r"podací\s+číslo",
    r"podaci\s+cislo",
)
PICKUP_LABELS: tuple[str, ...] = (
    r"číslo\s+balíku",
    r"cislo\s+baliku",
    r"číslo\s+balicku",
    r"pickup\s*(?:code|number)",
    r"kód\s+balíku",
    r" kod\s+baliku",
)


def _compile_labels(patterns: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(rf"(?:{pattern})", re.IGNORECASE) for pattern in patterns)


_LABELS: dict[str, tuple[re.Pattern[str], ...]] = {
    "tracking": _compile_labels(TRACKING_LABELS),
    "pickup": _compile_labels(PICKUP_LABELS),
}

# Generic shape heuristics used only for unlabelled prioritization. Carrier
# code-shape recognition/normalization lives in the provider seam.
_POSTAL_PRIORITY_RE = re.compile(r"^[A-Z]{1,2}\d{9,11}[A-Z]{0,2}$")
LABEL_SPLIT_RE = re.compile(r"\s*[:#]\s*")


class _HTMLTextExtractor(HTMLParser):
    """Minimal stdlib HTML-to-text converter used only on email parts."""

    _SKIP = frozenset({"script", "style", "head", "title"})
    _BREAK = frozenset({"br", "p", "div", "tr", "li", "table", "section"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK and not self._skip_depth:
            self.parts.append("\n")
        # Some carrier notices put the code only in the tracking link.
        # Keep the href transiently for extraction; it is never stored.
        for name, value in attrs:
            if (
                name.lower() == "href"
                and value
                and "/balik/" in value.lower()
                and not self._skip_depth
            ):
                self.parts.append(f"\n{value}\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK and not self._skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def normalize_text(value: str) -> str:
    """Normalise Unicode whitespace so labels and codes are easier to find."""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\xa0", " ").replace("\u200b", "").replace("\r\n", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def html_to_text(value: str) -> str:
    """Strip HTML tags from an email part, preserving line boundaries."""
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(value)
        extractor.close()
    except Exception:  # pragma: no cover - malformed HTML must not break polling
        return re.sub(r"<[^>]+>", " ", value)
    return normalize_text(extractor.text())


def decode_part(part: Message) -> str:
    """Decode a single ``email.message.Message`` part to text."""
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return normalize_text(raw if isinstance(raw, str) else "")
    charset = part.get_content_charset() or "utf-8"
    try:
        text = payload.decode(charset, errors="replace")
    except LookupError:
        text = payload.decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        return html_to_text(text)
    return normalize_text(text)


def decode_email_body(message: Message) -> str:
    """Return the best available text representation of a forwarded message."""
    plain: list[str] = []
    html: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain.append(decode_part(part))
        elif content_type == "text/html":
            html.append(decode_part(part))
    return normalize_text("\n".join(plain) if plain else "\n".join(html))


def _valid_code(candidate: str) -> bool:
    candidate = candidate.strip().upper()
    return (
        6 <= len(candidate) <= 15
        and candidate.isalnum()
        and sum(char.isdigit() for char in candidate) >= 3
    )


def _compile_extra(patterns: Iterable[str | re.Pattern[str]]) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns or ():
        try:
            compiled.append(
                pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
            )
        except re.error:
            # An invalid user pattern must not break every other parcel.
            continue
    return tuple(compiled)


def _recognized_codes(
    text: str,
    extra_patterns: Iterable[re.Pattern[str]] = (),
    *,
    allow_numeric: bool = True,
) -> list[str]:
    """Extract recognized tracking codes via the provider seam."""
    from .providers import extract_codes_from_text

    patterns = tuple(extra_patterns or ())
    recognized = extract_codes_from_text(text, extra_patterns=patterns)
    found: list[str] = []
    for item in recognized:
        code = (item.code or "").strip().upper()
        if not allow_numeric and item.numeric:
            continue
        if _valid_code(code) and code not in found:
            found.append(code)
    return found


def _codes_in(
    text: str,
    extra_patterns: Iterable[re.Pattern[str]] = (),
    *,
    allow_numeric: bool = True,
) -> list[str]:
    """Extract candidate tracking codes via provider recognition.

    ``allow_numeric=False`` is used for unlabelled scanning so a bare numeric
    pickup code (``Číslo balíku: 12345678``) is not mistaken for a tracking
    number. Explicit labels and user-provided regexes are still honoured.
    """
    return _recognized_codes(text, extra_patterns, allow_numeric=allow_numeric)


def _label_matches(line: str) -> list[tuple[str, re.Match[str]]]:
    """Find all recognised labels on a line, in reading order."""
    matches: list[tuple[str, re.Match[str]]] = []
    for kind, patterns in _LABELS.items():
        for pattern in patterns:
            for match in pattern.finditer(line):
                matches.append((kind, match))
    matches.sort(key=lambda item: item[1].start())
    # A longer label (e.g. "číslo balíku k podání") should win over a prefix
    # match ("číslo balíku") starting at the same position.
    result: list[tuple[str, re.Match[str]]] = []
    for kind, match in matches:
        if result and match.start() == result[-1][1].start():
            if len(match.group(0)) <= len(result[-1][1].group(0)):
                continue
            result[-1] = (kind, match)
        else:
            result.append((kind, match))
    return result


def _labelled_values(
    text: str, extra_patterns: Iterable[re.Pattern[str]]
) -> dict[str, list[str]]:
    """Map ``tracking``/``pickup`` to codes found next to their labels."""
    lines = text.splitlines()
    values: dict[str, list[str]] = {"tracking": [], "pickup": []}

    for index, line in enumerate(lines):
        matches = _label_matches(line)
        for position, (kind, match) in enumerate(matches):
            end = matches[position + 1][1].start() if position + 1 < len(matches) else len(line)
            region = LABEL_SPLIT_RE.split(line[match.end() : end], maxsplit=1)[-1]
            candidates = _codes_in(region, extra_patterns)
            if not candidates:
                # The sample email has the label on its own line followed by
                # a blank line and then the number.
                for follow in lines[index + 1 : index + 4]:
                    stripped = follow.strip()
                    if not stripped:
                        continue
                    if _label_matches(stripped):
                        break
                    candidates = _codes_in(stripped, extra_patterns)
                    if candidates:
                        break
            for candidate in candidates:
                if candidate not in values[kind]:
                    values[kind].append(candidate)
    return values


def find_tracking_numbers(
    text: str,
    extra_patterns: Iterable[str | re.Pattern[str]] = (),
    *,
    allow_numeric: bool = True,
) -> list[str]:
    """Return labelled tracking numbers, then unlabelled recognized codes."""
    normalized = normalize_text(text)
    patterns = _compile_extra(extra_patterns)
    labelled = _labelled_values(normalized, patterns)["tracking"]
    if labelled:
        return labelled

    from .providers import extract_codes_from_text

    recognized = extract_codes_from_text(normalized, extra_patterns=patterns)
    unlabelled: list[str] = []
    postal: list[str] = []
    carrier_codes: list[str] = []
    for item in recognized:
        code = (item.code or "").strip().upper()
        if not allow_numeric and item.numeric:
            continue
        if not _valid_code(code) or code in unlabelled:
            continue
        unlabelled.append(code)
        if _POSTAL_PRIORITY_RE.fullmatch(code):
            postal.append(code)
        if item.carrier and item.carrier != "unknown":
            carrier_codes.append(code)

    return postal or carrier_codes or (unlabelled[:1] if allow_numeric else [])


def parse_email_text(
    text: str, extra_patterns: Iterable[str | re.Pattern[str]] = ()
) -> ParsedEmail:
    """Parse the two numbers we care about out of a decoded email body."""
    normalized = normalize_text(text)
    patterns = _compile_extra(extra_patterns)
    values = _labelled_values(normalized, patterns)

    tracking = values["tracking"][0] if values["tracking"] else None
    if tracking is None:
        # Balíkovna pickup notices often have only "Číslo balíku" with a full
        # postal code. Accept that when it looks like a real tracking code,
        # but never promote a bare numeric pickup PIN to a tracked barcode.
        codes = find_tracking_numbers(normalized, patterns, allow_numeric=False)
        tracking = codes[0] if codes else None

    pickup = values["pickup"][0] if values["pickup"] else None
    carrier: str | None = None
    if tracking:
        # Imported lazily: the provider registry imports models, not parsers.
        from .providers import detect_provider

        provider = detect_provider(tracking)
        carrier = provider.CARRIER_ID if provider else None

    return ParsedEmail(tracking_code=tracking, pickup_code=pickup, carrier=carrier)


def decode_header_value(value: object) -> str:
    """Decode an RFC 2047 encoded header (``=?UTF-8?Q?...?=``) to text."""
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:  # pragma: no cover - malformed headers must not break polling
        return str(value)


def parse_message(
    message: Message, extra_patterns: Iterable[str | re.Pattern[str]] = ()
) -> ParsedEmail:
    """Parse an ``email.message.Message``; never retain subject or body.

    The subject is only a transient fallback when the body has no usable
    number (some notices redact the body but keep the code in the subject).
    """
    parsed = parse_email_text(decode_email_body(message), extra_patterns)
    if not parsed.tracking_code:
        subject = decode_header_value(message.get("Subject"))
        if subject:
            from_subject = parse_email_text(subject, extra_patterns)
            parsed.tracking_code = from_subject.tracking_code
            parsed.carrier = from_subject.carrier or parsed.carrier
    message_id = message.get("Message-ID")
    parsed.message_id = message_id.strip() if message_id else None
    return parsed


def canonical_field_names() -> tuple[str, ...]:
    """Return the canonical parcel keys exposed by this integration."""
    return (
        "carrier",
        "barcode",
        "sender",
        "receiver",
        "status",
        "raw_status",
        "delivered",
        "delivered_at",
        "planned_from",
        "planned_to",
        "pickup",
        "pickup_point",
        "pickup_code",
        "url",
        "weight",
        "dimensions",
        "history",
        "raw",
    )

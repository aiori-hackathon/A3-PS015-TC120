"""Standards-compliant DNS wire protocol message encoding and decoding (RFC 1035).

Provides direct, pure-Python wire format encoding and decoding for DNS queries and responses
over UDP sockets with strict validation of transaction IDs, flags, question sections,
and section bounds without using high-level OS resolver APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import re
import secrets
import socket
import struct
from typing import Dict, List, Optional, Set, Tuple

from ticnet.measurements.dns.models import get_rcode_name


# RFC 1123 Hostname / Domain Label Regex
LABEL_REGEX = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

# Standard DNS Record Types
RECORD_TYPE_MAP: Dict[str, int] = {
    "A": 1,
    "NS": 2,
    "CNAME": 5,
    "SOA": 6,
    "PTR": 12,
    "MX": 15,
    "TXT": 16,
    "AAAA": 28,
    "SRV": 33,
}

RECORD_NAME_MAP: Dict[int, str] = {v: k for k, v in RECORD_TYPE_MAP.items()}


# ============================================================================
# DNS Exceptions
# ============================================================================

class DNSError(Exception):
    """Base exception for DNS protocol errors."""
    pass


class DNSValidationError(DNSError):
    """Raised when query inputs (domain name, record type) fail validation."""
    pass


class DNSMalformedPacketError(DNSError):
    """Raised when a received DNS packet violates wire format specifications."""
    pass


class DNSMismatchedTransactionIDError(DNSError):
    """Raised when a response transaction ID does not match the active query."""
    pass


class DNSMismatchedQuestionError(DNSError):
    """Raised when a response question section does not match the queried domain or type."""
    pass


# ============================================================================
# Validation Helpers
# ============================================================================

def validate_dns_domain(domain: str) -> str:
    """Validate RFC 1035 / RFC 1123 domain name syntax.
    
    Checks:
    - Non-empty string.
    - Total length <= 253 characters.
    - Label count >= 1.
    - Each label 1..63 characters matching [A-Za-z0-9-], no leading/trailing hyphens.
    - No empty consecutive labels.
    """
    if not isinstance(domain, str) or not domain.strip():
        raise DNSValidationError("Domain name cannot be empty")

    cleaned = domain.strip().rstrip(".")
    if not cleaned:
        raise DNSValidationError("Domain name cannot be root '.' only")

    if len(cleaned) > 253:
        raise DNSValidationError(f"Domain name exceeds maximum length of 253 characters ({len(cleaned)})")

    labels = cleaned.split(".")
    for idx, label in enumerate(labels):
        if not label:
            raise DNSValidationError(f"Empty label at position {idx} in domain '{domain}'")
        if len(label) > 63:
            raise DNSValidationError(f"Label '{label}' exceeds maximum length of 63 characters ({len(label)})")
        if not LABEL_REGEX.match(label):
            raise DNSValidationError(
                f"Invalid characters in label '{label}'. Must be alphanumeric with interior hyphens."
            )

    return cleaned


def validate_dns_qtype(qtype: str) -> int:
    """Validate and convert record type name to numeric QTYPE integer."""
    if not isinstance(qtype, str) or not qtype.strip():
        raise DNSValidationError("Record type cannot be empty")
    upper_qtype = qtype.strip().upper()
    if upper_qtype not in RECORD_TYPE_MAP:
        raise DNSValidationError(
            f"Unsupported DNS record type '{qtype}'. Supported types: {sorted(RECORD_TYPE_MAP.keys())}"
        )
    return RECORD_TYPE_MAP[upper_qtype]


# ============================================================================
# Decoded Response Data Container
# ============================================================================

@dataclass(frozen=True)
class DecodedDNSResponse:
    """Decoded fields extracted from a validated DNS response datagram."""
    tx_id: int
    flags: int
    is_response: bool
    opcode: int
    is_authoritative: bool
    is_truncated: bool
    recursion_desired: bool
    recursion_available: bool
    rcode: int
    rcode_name: str
    qdcount: int
    ancount: int
    nscount: int
    arcount: int
    question_name: Optional[str] = None
    question_type: Optional[str] = None
    has_answer: bool = False
    answer_ips: List[str] = field(default_factory=list)


# ============================================================================
# Protocol Encoders and Decoders
# ============================================================================

def encode_dns_query(
    domain: str,
    qtype: str = "A",
    tx_id: Optional[int] = None,
) -> Tuple[bytes, int]:
    """Encode an RFC 1035 compliant DNS query message for UDP port 53 transport.
    
    Args:
        domain: Fully-qualified or relative domain name (e.g. 'example.com').
        qtype: Record type mnemonic (default: 'A').
        tx_id: Optional 16-bit transaction ID (generated cryptographically if None).
        
    Returns:
        Tuple of (wire_bytes, tx_id).
    """
    valid_domain = validate_dns_domain(domain)
    numeric_qtype = validate_dns_qtype(qtype)

    if tx_id is None:
        tx_id = secrets.randbelow(65536)
    elif not isinstance(tx_id, int) or tx_id < 0 or tx_id > 65535:
        raise DNSValidationError(f"Transaction ID must be a 16-bit integer (0..65535), got {tx_id}")

    # Header: ID, Flags (0x0100 = Recursion Desired, Standard Query), QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
    header = struct.pack("!HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)

    # Question QNAME: sequence of length-prefixed labels ending with null byte
    qname_parts: List[bytes] = []
    for label in valid_domain.split("."):
        encoded_label = label.encode("ascii")
        qname_parts.append(bytes([len(encoded_label)]) + encoded_label)
    qname_parts.append(b"\x00")
    qname_bytes = b"".join(qname_parts)

    # Question QTYPE and QCLASS (IN = 1)
    question_tail = struct.pack("!HH", numeric_qtype, 1)

    return header + qname_bytes + question_tail, tx_id


def _parse_dns_name(data: bytes, offset: int, max_hops: int = 10) -> Tuple[str, int]:
    """Parse a DNS domain name from packet data supporting RFC 1035 compression pointers.
    
    Args:
        data: Full DNS message buffer.
        offset: Starting byte offset in data.
        max_hops: Maximum allowed pointer indirection hops to prevent cyclic loops.
        
    Returns:
        Tuple of (domain_name_str, new_offset).
        
    Raises:
        DNSMalformedPacketError: On cyclic loops, out-of-bounds offsets, or invalid label formats.
    """
    labels: List[str] = []
    current = offset
    stepped = False
    new_offset = offset
    hops = 0
    visited_pointers: Set[int] = set()

    while True:
        if current >= len(data):
            raise DNSMalformedPacketError("Unexpected end of packet while parsing domain name")

        length = data[current]

        # Null byte: Root label terminating the name
        if length == 0:
            current += 1
            if not stepped:
                new_offset = current
            break

        # Check label type prefix (top two bits)
        # RFC 1035 section 4.1.4: 00 = standard label, 11 = compression pointer.
        # Other bit patterns (01, 10) are unallocated / invalid.
        label_tag = length & 0xC0
        if label_tag not in (0x00, 0xC0):
            raise DNSMalformedPacketError(f"Invalid DNS label type prefix: 0x{label_tag:02x}")

        # Compression pointer (top two bits set: 0xC0)
        if label_tag == 0xC0:
            if current + 1 >= len(data):
                raise DNSMalformedPacketError("Incomplete compression pointer in DNS packet")

            pointer_offset = ((length & 0x3F) << 8) | data[current + 1]

            # Pointer must point to an offset after the 12-byte header and within the packet data
            if pointer_offset < 12 or pointer_offset >= len(data):
                raise DNSMalformedPacketError(
                    f"Compression pointer offset out of bounds: {pointer_offset} (packet size: {len(data)})"
                )

            if pointer_offset in visited_pointers:
                raise DNSMalformedPacketError(
                    f"Cyclic compression pointer loop detected at offset {pointer_offset}"
                )
            visited_pointers.add(pointer_offset)

            hops += 1
            if hops > max_hops:
                raise DNSMalformedPacketError(
                    f"Exceeded maximum DNS compression pointer hops ({max_hops})"
                )

            if not stepped:
                new_offset = current + 2
                stepped = True
            current = pointer_offset
            continue

        # Ordinary label: length <= 63 octets
        if length > 63:
            raise DNSMalformedPacketError(f"DNS label length ({length}) exceeds maximum 63 octets")

        current += 1
        if current + length > len(data):
            raise DNSMalformedPacketError("Label data extends beyond packet boundaries")

        label_bytes = data[current : current + length]
        try:
            labels.append(label_bytes.decode("ascii"))
        except UnicodeDecodeError:
            labels.append(label_bytes.decode("latin-1", errors="replace"))

        current += length
        if not stepped:
            new_offset = current

    domain_name = ".".join(labels)
    if len(domain_name) > 253:
        raise DNSMalformedPacketError(
            f"Decompressed domain name exceeds 253 characters ({len(domain_name)})"
        )

    return domain_name, new_offset


def _parse_resource_records(
    data: bytes,
    offset: int,
    count: int,
    section_name: str,
) -> Tuple[List[Tuple[str, int, int, int, bytes]], int]:
    """Parse a sequence of DNS resource records (Answer, Authority, or Additional).
    
    Returns:
        Tuple of (list_of_records, new_offset).
        Each record is (name, type, class, ttl, rdata).
    """
    records: List[Tuple[str, int, int, int, bytes]] = []
    current = offset

    for _ in range(count):
        if current >= len(data):
            raise DNSMalformedPacketError(
                f"Packet truncated before reading all {count} records in {section_name} section"
            )
        name, current = _parse_dns_name(data, current)
        if current + 10 > len(data):
            raise DNSMalformedPacketError(
                f"Packet truncated in {section_name} record fixed header"
            )
        rtype, rclass, ttl, rdlength = struct.unpack("!HHIH", data[current : current + 10])
        current += 10
        if current + rdlength > len(data):
            raise DNSMalformedPacketError(
                f"RDATA length ({rdlength}) extends past packet boundary ({len(data)}) in {section_name} section"
            )
        rdata = data[current : current + rdlength]
        current += rdlength

        # Type-specific validation
        if rtype == 1:  # Type A
            if rdlength != 4:
                raise DNSMalformedPacketError(
                    f"Invalid RDATA length for A record: expected 4 octets, got {rdlength}"
                )
        elif rtype == 28:  # Type AAAA
            if rdlength != 16:
                raise DNSMalformedPacketError(
                    f"Invalid RDATA length for AAAA record: expected 16 octets, got {rdlength}"
                )

        records.append((name, rtype, rclass, ttl, rdata))

    return records, current


def decode_dns_response(
    data: bytes,
    expected_tx_id: int,
    expected_domain: str,
    expected_qtype: str = "A",
) -> DecodedDNSResponse:
    """Decode and strictly validate an incoming DNS response datagram against active query context.
    
    Args:
        data: Raw UDP datagram bytes.
        expected_tx_id: Expected 16-bit transaction ID.
        expected_domain: Queried domain name.
        expected_qtype: Queried record type (default: 'A').
        
    Returns:
        DecodedDNSResponse dataclass with extracted fields.
        
    Raises:
        DNSMalformedPacketError: For truncated headers, invalid flags, corrupt sections, or bound violations.
        DNSMismatchedTransactionIDError: If response transaction ID does not match.
        DNSMismatchedQuestionError: If question section does not match queried name/type/class.
    """
    if len(data) < 12:
        raise DNSMalformedPacketError(f"Response datagram ({len(data)} bytes) is shorter than DNS header (12 bytes)")

    tx_id, flags, qdcount, ancount, nscount, arcount = struct.unpack("!HHHHHH", data[:12])

    # 1. Transaction ID check
    if tx_id != expected_tx_id:
        raise DNSMismatchedTransactionIDError(
            f"Transaction ID mismatch: expected {expected_tx_id}, got {tx_id}"
        )

    # 2. Header Flags validation
    is_response = bool((flags >> 15) & 0x1)
    if not is_response:
        raise DNSMalformedPacketError("Datagram is not a DNS response (QR flag is 0)")

    opcode = (flags >> 11) & 0xF
    if opcode != 0:
        raise DNSMalformedPacketError(f"Unexpected DNS response opcode: {opcode}")

    is_authoritative = bool((flags >> 10) & 0x1)
    is_truncated = bool((flags >> 9) & 0x1)
    recursion_desired = bool((flags >> 8) & 0x1)
    recursion_available = bool((flags >> 7) & 0x1)
    rcode = flags & 0xF
    rcode_name = get_rcode_name(rcode)

    # 3. Question Section Parsing & Validation
    offset = 12
    qname: Optional[str] = None
    qtype_str: Optional[str] = None

    if qdcount == 0:
        if is_truncated:
            return DecodedDNSResponse(
                tx_id=tx_id,
                flags=flags,
                is_response=is_response,
                opcode=opcode,
                is_authoritative=is_authoritative,
                is_truncated=True,
                recursion_desired=recursion_desired,
                recursion_available=recursion_available,
                rcode=rcode,
                rcode_name=rcode_name,
                qdcount=qdcount,
                ancount=ancount,
                nscount=nscount,
                arcount=arcount,
                has_answer=False,
            )
        raise DNSMalformedPacketError("DNS response has no question section (qdcount=0)")
    elif qdcount > 1:
        raise DNSMalformedPacketError(f"Unsupported question count in response: {qdcount} (expected 1)")

    # Parse Question
    try:
        qname, offset = _parse_dns_name(data, offset)
    except DNSMalformedPacketError:
        if is_truncated:
            return DecodedDNSResponse(
                tx_id=tx_id,
                flags=flags,
                is_response=is_response,
                opcode=opcode,
                is_authoritative=is_authoritative,
                is_truncated=True,
                recursion_desired=recursion_desired,
                recursion_available=recursion_available,
                rcode=rcode,
                rcode_name=rcode_name,
                qdcount=qdcount,
                ancount=ancount,
                nscount=nscount,
                arcount=arcount,
                has_answer=False,
            )
        raise

    if offset + 4 > len(data):
        if is_truncated:
            return DecodedDNSResponse(
                tx_id=tx_id,
                flags=flags,
                is_response=is_response,
                opcode=opcode,
                is_authoritative=is_authoritative,
                is_truncated=True,
                recursion_desired=recursion_desired,
                recursion_available=recursion_available,
                rcode=rcode,
                rcode_name=rcode_name,
                qdcount=qdcount,
                ancount=ancount,
                nscount=nscount,
                arcount=arcount,
                question_name=qname,
                has_answer=False,
            )
        raise DNSMalformedPacketError("Packet truncated in question section")

    qtype_num, qclass_num = struct.unpack("!HH", data[offset : offset + 4])
    offset += 4
    qtype_str = RECORD_NAME_MAP.get(qtype_num, f"TYPE{qtype_num}")

    # Check question class (must be IN = 1)
    if qclass_num != 1:
        raise DNSMismatchedQuestionError(
            f"Question class mismatch: expected IN (1), got {qclass_num}"
        )

    # Check question matches query
    clean_expected_domain = expected_domain.strip().rstrip(".").lower()
    if qname.lower() != clean_expected_domain:
        raise DNSMismatchedQuestionError(
            f"Question name mismatch: expected '{clean_expected_domain}', got '{qname.lower()}'"
        )
    expected_type_num = validate_dns_qtype(expected_qtype)
    if qtype_num != expected_type_num:
        raise DNSMismatchedQuestionError(
            f"Question record type mismatch: expected {expected_qtype} ({expected_type_num}), got {qtype_num}"
        )

    # 4. If truncated (TC=1), do not parse incomplete answer records as complete
    if is_truncated:
        return DecodedDNSResponse(
            tx_id=tx_id,
            flags=flags,
            is_response=is_response,
            opcode=opcode,
            is_authoritative=is_authoritative,
            is_truncated=True,
            recursion_desired=recursion_desired,
            recursion_available=recursion_available,
            rcode=rcode,
            rcode_name=rcode_name,
            qdcount=qdcount,
            ancount=ancount,
            nscount=nscount,
            arcount=arcount,
            question_name=qname,
            question_type=qtype_str,
            has_answer=False,
            answer_ips=[],
        )

    # 5. Answer Section Parsing
    answer_ips: List[str] = []
    has_answer = False

    if ancount > 0:
        ans_records, offset = _parse_resource_records(data, offset, ancount, "answer")
        for _r_name, r_type, _r_class, _r_ttl, rdata in ans_records:
            if r_type == expected_type_num:
                if r_type == 1 and len(rdata) == 4:
                    ip_str = socket.inet_ntoa(rdata)
                    answer_ips.append(ip_str)
                    has_answer = True
                elif r_type == 28 and len(rdata) == 16:
                    ip_str = socket.inet_ntop(socket.AF_INET6, rdata)
                    answer_ips.append(ip_str)
                    has_answer = True
                else:
                    has_answer = True

    # 6. Authority Section Parsing
    if nscount > 0:
        _ns_records, offset = _parse_resource_records(data, offset, nscount, "authority")

    # 7. Additional Section Parsing
    if arcount > 0:
        _ar_records, offset = _parse_resource_records(data, offset, arcount, "additional")

    return DecodedDNSResponse(
        tx_id=tx_id,
        flags=flags,
        is_response=is_response,
        opcode=opcode,
        is_authoritative=is_authoritative,
        is_truncated=False,
        recursion_desired=recursion_desired,
        recursion_available=recursion_available,
        rcode=rcode,
        rcode_name=rcode_name,
        qdcount=qdcount,
        ancount=ancount,
        nscount=nscount,
        arcount=arcount,
        question_name=qname,
        question_type=qtype_str,
        has_answer=has_answer,
        answer_ips=answer_ips,
    )

import re
from urllib.parse import parse_qsl, unquote_plus, urlsplit


UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
SERVICE_START_PREFIXES = ("r_", "section_")


def _extract_start_payload(value: str) -> str:
    raw = unquote_plus(str(value or "").strip())
    if not raw:
        return ""

    if raw.startswith("/start"):
        raw = raw[6:].strip()

    if re.match(r"^(?:https?://|tg://)", raw, flags=re.IGNORECASE):
        parsed_url = urlsplit(raw)
        query = dict(parse_qsl(parsed_url.query, keep_blank_values=False))
        return str(query.get("start") or query.get("startapp") or "").strip()

    if raw.startswith("?"):
        raw = raw[1:]
    parsed_query = dict(parse_qsl(raw, keep_blank_values=False)) if "=" in raw else {}
    if parsed_query.get("start") or parsed_query.get("startapp"):
        return str(parsed_query.get("start") or parsed_query.get("startapp") or "").strip()
    return raw


def parse_start_tracking(value: str | None) -> tuple[str | None, dict[str, str]]:
    payload = _extract_start_payload(value or "")
    if not payload:
        return None, {}
    if payload.startswith(SERVICE_START_PREFIXES):
        return payload, {}

    utm_data: dict[str, str] = {}
    if "=" in payload:
        parsed = dict(parse_qsl(payload.lstrip("?"), keep_blank_values=False))
        utm_data = {
            key: str(parsed[key]).strip()
            for key in UTM_KEYS
            if parsed.get(key) and str(parsed[key]).strip()
        }
        if not utm_data and parsed.get("source"):
            utm_data["utm_source"] = str(parsed["source"]).strip()
    elif payload.startswith("utm_"):
        if "__" not in payload and payload.count("_") == 1:
            source = payload[4:].strip()
            if source:
                utm_data["utm_source"] = source
        else:
            for chunk in payload.split("__"):
                for key in UTM_KEYS:
                    prefix = f"{key}_"
                    if chunk.startswith(prefix):
                        item = chunk[len(prefix):].strip()
                        if item:
                            utm_data[key] = item
                        break

    # Anything external that was not a service/referral payload is still a
    # useful acquisition source. Preserve it instead of silently dropping it.
    if not utm_data:
        source = payload.strip()[:255]
        if source:
            utm_data["utm_source"] = source

    return payload[:255], utm_data

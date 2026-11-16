import phonenumbers


def normalize_phone_number(raw_phone: str, default_region: str = "NG") -> str:
    """
    Normalizes any input phone number to E.164 format (+234...).
    Raises ValueError if phone number is invalid.
    """
    clean_phone = raw_phone.strip()
    try:
        parsed = phonenumbers.parse(clean_phone, default_region)
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError(f"Invalid phone number: {raw_phone}")
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException as e:
        raise ValueError(f"Failed to parse phone number '{raw_phone}': {e}") from e


def kobo_to_naira_str(amount_kobo: int) -> str:
    """Formats an integer kobo amount to a friendly Naira string (e.g. ₦5,000.00)."""
    naira = amount_kobo / 100
    return f"₦{naira:,.2f}"

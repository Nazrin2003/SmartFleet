import re
from django.core.exceptions import ValidationError


class CharacterNumberValidator:
    def validate(self, password, user=None):
        if not re.search(r"[A-Za-z]", password or ""):
            raise ValidationError("Password must contain at least one letter.")
        if not re.search(r"\d", password or ""):
            raise ValidationError("Password must contain at least one number.")
        if not re.search(r"[^\w\s]", password or ""):
            raise ValidationError("Password must contain at least one symbol.")

    def get_help_text(self):
        return "Your password must contain at least one letter, one number, and one symbol."

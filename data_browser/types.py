import json
import re
from functools import lru_cache

import dateutil.parser
from django.conf import settings
from django.utils import dateparse, html, timezone

from .common import all_subclasses, get_optimal_decimal_places

ASC, DSC = "asc", "dsc"


class TypeMeta(type):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.raw_type and self.element_type:
            assert self.raw_type.element_type == self.element_type.raw_type

    def __repr__(cls):
        return cls.__name__

    @property
    def default_lookup(cls):
        lookups = cls.lookups
        return list(lookups)[0] if lookups else None

    @property
    def lookups(cls):
        return {name: type_.name for name, type_ in cls._lookups().items()}

    @property
    def name(cls):
        name = cls.__name__.lower()
        assert name.endswith("type")
        return name[: -len("type")]


class BaseType(metaclass=TypeMeta):
    default_value = None
    choices = ()
    raw_type = None
    element_type = None

    def __init__(self):
        assert False

    @staticmethod
    def _lookups():
        return {}

    @staticmethod
    def _get_formatter(choices):
        assert not choices
        return lambda value: value

    @classmethod
    def get_formatter(cls, choices):
        return cls._get_formatter(choices)

    @staticmethod
    def _parse(value, choices):
        return value

    @classmethod
    def parse(cls, lookup, value, choices):
        lookups = cls.lookups
        if lookup not in lookups:
            return None, f"Bad lookup '{lookup}' expected {lookups}"
        else:
            type_ = TYPES[lookups[lookup]]
            try:
                return type_._parse(value, choices), None
            except Exception as e:
                err_message = str(e) if str(e) else repr(e)
                return None, err_message

    @staticmethod
    def get_format_hints(name, data):
        return {}


class StringType(BaseType):
    default_value = ""

    @classmethod
    def _lookups(cls):
        return {
            "equals": cls,
            "contains": cls,
            "starts_with": cls,
            "ends_with": cls,
            "regex": RegexType,
            "not_equals": cls,
            "not_contains": cls,
            "not_starts_with": cls,
            "not_ends_with": cls,
            "not_regex": RegexType,
            "is_null": IsNullType,
        }


class NumberType(BaseType):
    default_value = 0

    @classmethod
    def _lookups(cls):
        return {
            "equals": cls,
            "not_equals": cls,
            "gt": cls,
            "gte": cls,
            "lt": cls,
            "lte": cls,
            "is_null": IsNullType,
        }

    @staticmethod
    def _get_formatter(choices):
        assert not choices
        return lambda value: None if value is None else float(value)

    @staticmethod
    def _parse(value, choices):
        return float(value)

    @staticmethod
    def get_format_hints(name, data):
        nums = [
            row[name] for row in data if row and row[name] and abs(row[name] > 0.0001)
        ]
        dp = get_optimal_decimal_places(nums)
        return {
            "minimumFractionDigits": dp,
            "maximumFractionDigits": dp,
            "significantFigures": 3,
            "lowCutOff": 0.0001,
            "highCutOff": 1e10,
        }


class RegexType(BaseType):
    default_value = ".*"

    @staticmethod
    @lru_cache(maxsize=None)
    def _parse(value, choices):
        from django.contrib.contenttypes.models import ContentType
        from django.db.transaction import atomic

        # this is dirty
        # we need to check if the regex is going to cause a db exception
        # and not kill any in progress transaction as we check
        with atomic():
            list(ContentType.objects.filter(model__regex=value))
        return value


class DurationType(BaseType):
    default_value = ""

    @classmethod
    def _lookups(cls):
        return {
            "equals": cls,
            "not_equals": cls,
            "gt": cls,
            "gte": cls,
            "lt": cls,
            "lte": cls,
            "is_null": IsNullType,
        }

    @staticmethod
    def _parse(value, choices):
        if value.count(":") == 1:
            value += ":0"

        res = dateparse.parse_duration(value)
        assert res is not None, "Duration value should be 'DD HH:MM:SS'"
        return res

    @staticmethod
    def _get_formatter(choices):
        assert not choices
        return lambda value: None if value is None else str(value)


class DateTypeMixin:
    @classmethod
    def _lookups(cls):
        return {
            "equals": cls,
            "not_equals": cls,
            "gt": cls,
            "gte": cls,
            "lt": cls,
            "lte": cls,
            "is_null": IsNullType,
        }

    @classmethod
    def _parse(cls, value, choices):
        d8 = r"(\d{8})"
        d422 = r"(\d{4}[^\d]*\d{2}[^\d]*\d{2})"
        if re.match(r"[^\d]*(" + d8 + "|" + d422 + ")", value):
            # looks like some kinda iso date, roll with the defaults
            res = dateutil.parser.parse(value)
        else:
            res = {
                dateutil.parser.parse(value, dayfirst=False, yearfirst=False),
                dateutil.parser.parse(value, dayfirst=True, yearfirst=False),
                dateutil.parser.parse(value, dayfirst=False, yearfirst=True),
                dateutil.parser.parse(value, dayfirst=True, yearfirst=True),
            }
            assert len(res) == 1, "Ambiguous value"
            res = res.pop()
        return timezone.make_aware(res)


class DateTimeType(DateTypeMixin, BaseType):
    default_value = "now"

    @classmethod
    def _parse(cls, value, choices):
        if value.lower().strip() == "now":
            return timezone.now()
        return super()._parse(value, choices)

    @staticmethod
    def _get_formatter(choices):
        assert not choices
        if settings.USE_TZ:
            return (
                lambda value: None if value is None else str(timezone.make_naive(value))
            )
        else:
            return lambda value: None if value is None else str(value)


class DateType(DateTypeMixin, BaseType):
    default_value = "today"

    @classmethod
    def _parse(cls, value, choices):
        if value.lower().strip() == "today":
            return timezone.now().date()
        return super()._parse(value, choices).date()

    @staticmethod
    def _get_formatter(choices):
        assert not choices
        return lambda value: None if value is None else str(value)


class HTMLType(StringType):
    @staticmethod
    def _lookups():
        return {}

    @staticmethod
    def _get_formatter(choices):
        assert not choices
        return lambda value: None if value is None else html.conditional_escape(value)


class BooleanType(BaseType):
    default_value = True

    @classmethod
    def _lookups(cls):
        return {"equals": cls, "not_equals": cls, "is_null": IsNullType}

    @staticmethod
    def _parse(value, choices):
        value = value.lower()
        if value == "true":
            return True
        elif value == "false":
            return False
        else:
            raise ValueError("Expected 'true' or 'false'")

    @staticmethod
    def _get_formatter(choices):
        assert not choices
        return lambda value: None if value is None else bool(value)


class UnknownType(BaseType):
    @staticmethod
    def _get_formatter(choices):
        assert not choices
        return lambda value: None if value is None else str(value)

    @staticmethod
    def _lookups():
        return {"is_null": IsNullType}


def _json_loads(value):
    try:
        return json.loads(value.strip())
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON value")


class JSONFieldType(BaseType):
    default_value = "|"

    @staticmethod
    def _parse(value, choices):
        value = value.strip()
        if "|" not in value:
            raise ValueError("Missing seperator '|'")
        field, value = value.split("|", 1)
        if not field:
            raise ValueError("Invalid field name")
        return [field, _json_loads(value)]


class JSONType(BaseType):
    @classmethod
    def _lookups(cls):
        return {
            "equals": cls,
            "has_key": StringType,
            "field_equals": JSONFieldType,
            "not_equals": cls,
            "not_has_key": StringType,
            "not_field_equals": JSONFieldType,
            "is_null": IsNullType,
        }

    @staticmethod
    def _parse(value, choices):
        return _json_loads(value)


class ChoiceTypeMixin:
    default_value = None

    @classmethod
    def _get_formatter(cls, choices):
        assert choices
        choices = dict(choices)
        return lambda value: choices.get(value, value)

    @classmethod
    def _parse(cls, value, choices):
        assert choices
        choices = {v: k for k, v in choices}
        if value not in choices:
            raise ValueError(f"Unknown choice '{value}'")
        return choices[value]

    @classmethod
    def _lookups(cls):
        return {"equals": cls, "not_equals": cls, "is_null": IsNullType}


class StringChoiceType(ChoiceTypeMixin, BaseType):
    raw_type = StringType


class NumberChoiceType(ChoiceTypeMixin, BaseType):
    raw_type = NumberType


class IsNullType(ChoiceTypeMixin, BaseType):
    choices = [(True, "IsNull"), (False, "NotNull"), (None, "MissingField")]
    default_value = choices[0][1]

    @staticmethod
    def _lookups():
        return {"equals": IsNullType}

    @classmethod
    def _get_formatter(cls, choices):
        return super()._get_formatter(cls.choices)

    @classmethod
    def _parse(cls, value, choices):
        return super()._parse(value, cls.choices)


class ArrayTypeMixin:
    default_value = None

    @classmethod
    def _get_formatter(cls, choices):  # pragma: postgres
        element_formatter = cls.element_type._get_formatter(choices)
        return (
            lambda value: None
            if value is None
            else json.dumps([element_formatter(v) for v in value])
        )

    @classmethod
    def _lookups(cls):
        return {
            "equals": cls,
            "contains": cls.element_type,
            "length": NumberType,
            "not_equals": cls,
            "not_contains": cls.element_type,
            "not_length": NumberType,
            "is_null": IsNullType,
        }

    @classmethod
    def _parse(cls, value, choices):
        value = _json_loads(value)
        if not isinstance(value, list):
            raise ValueError("Expected a list")
        return [cls.element_type._parse(v, choices) for v in value]


class StringArrayType(ArrayTypeMixin, BaseType):
    element_type = StringType


class NumberArrayType(ArrayTypeMixin, BaseType):
    element_type = NumberType


class StringChoiceArrayType(ArrayTypeMixin, BaseType):
    element_type = StringChoiceType
    raw_type = StringArrayType


class NumberChoiceArrayType(ArrayTypeMixin, BaseType):
    element_type = NumberChoiceType
    raw_type = NumberArrayType


TYPES = {cls.name: cls for cls in all_subclasses(BaseType)}

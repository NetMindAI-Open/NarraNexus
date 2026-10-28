"""
@file_name: test_trigger_config.py
@author: Bin Liang
@date: 2026-04-21
@description: Validator tests for TriggerConfig timezone protocol (v2).
"""
import pytest
from datetime import datetime
from pydantic import ValidationError

from narranexus.platform.schema.job_schema import TriggerConfig


class TestTriggerConfigTimezoneRequired:
    def test_one_off_requires_timezone(self):
        with pytest.raises(ValidationError, match="timezone"):
            TriggerConfig(run_at=datetime(2026, 5, 1, 8, 0, 0))

    def test_cron_requires_timezone(self):
        with pytest.raises(ValidationError, match="timezone"):
            TriggerConfig(cron="0 8 * * *")

    def test_interval_requires_timezone(self):
        with pytest.raises(ValidationError, match="timezone"):
            TriggerConfig(interval_seconds=3600)

    def test_valid_one_off(self):
        tc = TriggerConfig(
            run_at=datetime(2026, 5, 1, 8, 0, 0),
            timezone="Asia/Shanghai",
        )
        assert tc.timezone == "Asia/Shanghai"

    def test_valid_cron(self):
        tc = TriggerConfig(cron="0 8 * * *", timezone="America/New_York")
        assert tc.timezone == "America/New_York"

    def test_valid_interval(self):
        tc = TriggerConfig(interval_seconds=3600, timezone="UTC")
        assert tc.timezone == "UTC"
        assert tc.interval_seconds == 3600


class TestTriggerConfigRunAtNaive:
    def test_rejects_aware_run_at(self):
        from datetime import timezone as dt_tz
        aware = datetime(2026, 5, 1, 8, 0, 0, tzinfo=dt_tz.utc)
        with pytest.raises(ValidationError, match="naive"):
            TriggerConfig(run_at=aware, timezone="Asia/Shanghai")


class TestTriggerConfigIANAValid:
    def test_rejects_invalid_iana(self):
        with pytest.raises(ValidationError, match="not a valid IANA"):
            TriggerConfig(cron="0 8 * * *", timezone="CST")

    def test_rejects_empty_timezone(self):
        with pytest.raises(ValidationError):
            TriggerConfig(cron="0 8 * * *", timezone="")


class TestJobEntityNewFields:
    def test_job_has_beta_fields(self):
        from narranexus.platform.schema.job_schema import Job
        fields = Job.model_fields
        assert "next_run_at_local" in fields
        assert "next_run_tz" in fields
        assert "last_run_at_local" in fields
        assert "last_run_tz" in fields


class TestTriggerConfigFromStoredDict:
    """B-15: rows written before the timezone-required validator existed
    (e.g. `{'cron': '0 13 * * 1-5'}`) must still load — `TriggerConfig(**data)`
    raises on them (by design, for NEW writes), so the read path needs a
    tolerant constructor that defaults the missing timezone in memory only.
    """

    def test_missing_timezone_defaults_to_utc(self):
        tc = TriggerConfig.from_stored_dict({"cron": "0 13 * * 1-5"})
        assert tc.cron == "0 13 * * 1-5"
        assert tc.timezone == "UTC"

    def test_present_timezone_is_kept(self):
        tc = TriggerConfig.from_stored_dict(
            {"cron": "0 13 * * 1-5", "timezone": "Asia/Shanghai"}
        )
        assert tc.timezone == "Asia/Shanghai"

    def test_no_time_bearing_field_stays_untouched(self):
        """A row with no cron/interval_seconds/run_at/end_at never needed a
        timezone in the first place — don't force one on it."""
        tc = TriggerConfig.from_stored_dict({})
        assert tc.timezone is None

    def test_still_rejects_invalid_iana_even_when_defaulting(self):
        """Defaulting only fills in a MISSING timezone; an explicit garbage
        value must still fail loudly, not be silently swallowed."""
        with pytest.raises(ValidationError, match="not a valid IANA"):
            TriggerConfig.from_stored_dict({"cron": "0 8 * * *", "timezone": "CST"})

    def test_none_input_is_treated_as_empty(self):
        tc = TriggerConfig.from_stored_dict(None)
        assert tc.timezone is None


class TestTimeBearingFieldsSingleList:
    """review I11: the validator and from_stored_dict read ONE ClassVar, so a
    new time-bearing field cannot be learned by the write side alone."""

    SAMPLE = {"run_at": "2026-09-01T08:00:00", "cron": "0 8 * * *",
              "interval_seconds": 3600, "end_at": "2026-12-31T00:00:00"}

    def test_every_time_bearing_field_is_covered(self):
        assert set(TriggerConfig.TIME_BEARING_FIELDS) == set(self.SAMPLE)

    @pytest.mark.parametrize("field", TriggerConfig.TIME_BEARING_FIELDS)
    def test_constructor_requires_timezone_for_each_field(self, field):
        with pytest.raises(ValidationError):
            TriggerConfig(**{field: self.SAMPLE[field]})

    @pytest.mark.parametrize("field", TriggerConfig.TIME_BEARING_FIELDS)
    def test_from_stored_dict_defaults_timezone_for_each_field(self, field):
        tc = TriggerConfig.from_stored_dict({field: self.SAMPLE[field]})
        assert tc.timezone == "UTC"

    def test_explicit_empty_timezone_is_not_repaired(self):
        """review M1: '' is a present-but-bad value — the IANA validator must
        reject it; only an ABSENT (missing / None) timezone is defaulted."""
        with pytest.raises(ValidationError):
            TriggerConfig.from_stored_dict({"cron": "0 8 * * *", "timezone": ""})

    def test_explicit_none_timezone_is_treated_as_absent(self):
        tc = TriggerConfig.from_stored_dict({"cron": "0 8 * * *", "timezone": None})
        assert tc.timezone == "UTC"


class TestTriggerConfigConstructor:
    def test_constructor_still_rejects_missing_timezone(self):
        """The strict constructor is untouched — new/updated jobs still must
        supply timezone explicitly (regression guard for TestTriggerConfig
        TimezoneRequired above)."""
        with pytest.raises(ValidationError, match="timezone"):
            TriggerConfig(cron="0 8 * * *")

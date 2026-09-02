import pytest
from switchyard_config.routes import (
    validate_toml_identifier,
    validate_toml_string_value,
    validate_env_var_name,
    validate_int_field,
    validate_float_field,
    validate_weights,
)
from switchyard_config.validation import ValidationError

def test_validate_toml_identifier_invalid():
    with pytest.raises(ValidationError) as exc:
        validate_toml_identifier("invalid!", "test")
    assert "may only contain" in str(exc.value)

def test_validate_toml_string_value_banned():
    with pytest.raises(ValidationError) as exc:
        validate_toml_string_value('bad"quote', 'field')
    assert "may not contain" in str(exc.value)

def test_validate_toml_string_value_ok():
    validate_toml_string_value("good-value", 'field')

def test_validate_env_var_name_invalid():
    with pytest.raises(ValidationError) as exc:
        validate_env_var_name('1BAD', 'env')
    assert "must be a valid env var name" in str(exc.value)

def test_validate_env_var_name_empty():
    # Empty name is allowed (means no auth)
    validate_env_var_name('', 'env')

def test_validate_int_field_invalid():
    with pytest.raises(ValidationError) as exc:
        validate_int_field("abc", "Count")
    assert "must be a whole number" in str(exc.value)

def test_validate_int_field_below_min():
    with pytest.raises(ValidationError) as exc:
        validate_int_field("0", "Count", minimum=1)
    assert "must be >=" in str(exc.value)

def test_validate_int_field_empty_ok():
    validate_int_field("", "Count", minimum=1)

def test_validate_float_field_invalid():
    with pytest.raises(ValidationError) as exc:
        validate_float_field("notanumber", "Threshold")
    assert "must be a number" in str(exc.value)

def test_validate_float_field_empty_ok():
    validate_float_field("", "Threshold", minimum=0.0)

def test_validate_weights_not_a_number():
    with pytest.raises(ValidationError) as exc:
        validate_weights("1,abc,3", "Weights")
    assert "is not a number" in str(exc.value)

def test_validate_weights_count_mismatch():
    with pytest.raises(ValidationError) as exc:
        validate_weights("1,2,3", "Weights", expected_count=2)
    assert "counts must match" in str(exc.value)

def test_validate_weights_empty_ok():
    validate_weights("", "Weights", expected_count=2)

def test_validation_error_has_field_and_message():
    with pytest.raises(ValidationError) as exc:
        validate_int_field("abc", "Count", minimum=1)
    assert exc.value.field == "Count"
    assert "must be a whole number" in exc.value.message
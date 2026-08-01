from ret2console.flag import extract_flag, should_exclude_from_history


def test_valid_flags():
    for line in ["flag{abc}", "moectf{1234}", "xxxx{yyyy}", "FLAG{hello world}", "foo{bar_baz}"]:
        assert extract_flag(line) == line.strip(), line


def test_trimmed_flags():
    assert extract_flag("  flag{abc}  ") == "flag{abc}"
    assert extract_flag("flag{abc}\n") == "flag{abc}"


def test_invalid():
    for line in ["flag{}", "flag{ab{cd}", "not a flag", "flag{abc} extra", "echo flag{abc}", "flag", "{abc}"]:
        assert extract_flag(line) is None, line


def test_history_exclusion():
    assert should_exclude_from_history("flag{secret}")
    assert not should_exclude_from_history("ls /contests")

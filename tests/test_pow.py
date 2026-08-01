import hashlib

from ret2console.api.pow import PowChallenge, solve


def test_solve_meets_criteria():
    challenge = PowChallenge(captcha_id="cid", difficulty=4, seed="abcdef1234567890")
    answer = solve(challenge)
    assert answer.startswith("abcdef1234567890")
    assert hashlib.sha256(answer.encode()).hexdigest().startswith("0000")


def test_parse_challenge_string():
    c = PowChallenge.parse("cid", "4#some-seed-16ch")
    assert c.difficulty == 4
    assert c.seed == "some-seed-16ch"

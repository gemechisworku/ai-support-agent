"""T-AGENT-03 · AGENT-R3: the deterministic interrupt detector (phrase tables, negations, priorities)."""

import time
import typing

import pytest

from supportagent.agent import base
from supportagent.agent.interrupts import (
    INTENT_FOR,
    InterruptKind,
    detect_interrupt,
    detect_interrupts,
    is_misunderstanding,
)
from supportagent.contracts.outcome import Intent

# ≥ 5 positive and ≥ 3 negative phrasings per kind (AGENT-03 acceptance).
POSITIVE: dict[str, list[str]] = {
    "human_requested": [
        "Get me a human",
        "I want to talk to a person",
        "Can you transfer me to a representative?",
        "Let me speak with a manager please",
        "I need a real person",
        "Human please",
        "Is there a real person I can talk to?",
        "put me through to your supervisor",
    ],
    "dispute": [
        "I dispute this charge",
        "I'm disputing the balance you just told me",
        "I never took out that loan",
        "I don't owe this money",
        "That's not my loan",
        "This is fraud",
        "That late fee is wrong",
        "I got charged twice for the same payment",
    ],
    "stop_calling": [
        "Stop calling me",
        "Please don't call me again",
        "Do not call me anymore",
        "Take me off your list",
        "Remove my number from your system",
        "Never call me again",
        "I want you to stop contacting me",
        "No more calls",
        "Leave me alone",
        "Put me on the do not call list",
    ],
    "third_party": [
        "This is his wife",
        "I'm her daughter",
        "He's not home right now",
        "She isn't available",
        "He doesn't live here anymore",
        "Can I take a message?",
        "I'm calling on his behalf",
        "You're speaking to his mother",
    ],
    "legal": [
        "I'm going to call my lawyer",
        "My attorney will contact you",
        "I'm filing for bankruptcy",
        "I filed bankruptcy last month",
        "I'll sue you",
        "See you in court",
        "This is going to be a lawsuit",
        "I'm in chapter 7",
    ],
    "complaint": [
        "I want to file a complaint",
        "I'd like to complain about this",
        "This is unacceptable",
        "Your service is terrible",
        "I'm very unhappy with the way I was treated",
        "I'm going to report you to the regulator",
        "I'll go to the ombudsman",
    ],
    "hardship": [
        "I lost my job last month",
        "I was laid off in October",
        "I can't afford to pay right now",
        "I'm out of work",
        "I'm struggling financially",
        "My husband passed away",
        "I have a lot of medical bills",
        "My hours were cut at work",
    ],
}

NEGATIVE: dict[str, list[str]] = {
    "human_requested": [
        "I don't want to talk to a human, I just want to pay",
        "I already talked to a person yesterday",
        "The person who called me earlier didn't leave a message",
        "I can talk to someone at the branch tomorrow",
        "I don't need a manager",
    ],
    "dispute": [
        "I don't want to dispute anything, just pay",
        "There's no dispute, I just forgot",
        "I'm not disputing it",
        "That fee seems fair",
        "I agree with the balance",
        "I'll pay the amount you mentioned",
    ],
    "stop_calling": [
        "Please don't call me at work, call after six",
        "Can you call me back tomorrow afternoon?",
        "I stopped by the branch yesterday",
        "Don't stop calling me until it's sorted, I want to pay",
        "Keep calling until you reach him",
        "I don't want you to stop calling, I want to settle this",
    ],
    "third_party": [
        "Yes, this is he",
        "I'm the account holder",
        "My wife and I both have loans here",
        "This is Frank speaking",
        "Is this about my loan?",
    ],
    "legal": [
        "I don't have a lawyer, I just have a question",
        "No lawsuit needed, I'm paying today",
        "The issue is my paycheck was late",
        "I'm not bankrupt, I'm fine",
        "I don't think I need an attorney",
    ],
    "complaint": [
        "I have no complaints",
        "Thanks, the service is great",
        "I'm not upset, just confused",
        "I'm happy with the bank",
        "I'm not unhappy with the service",
    ],
    "hardship": [
        "I'm not struggling, I just forgot the due date",
        "I didn't lose my job, the payment was simply late",
        "I have a job and can afford the payment",
        "No hardship, I just forgot",
        "The hospital is across the street from my house",
    ],
}


def test_every_kind_has_the_required_phrasings() -> None:
    kinds = set(typing.get_args(InterruptKind))
    assert set(POSITIVE) == kinds == set(NEGATIVE)
    for kind in kinds:
        assert len(POSITIVE[kind]) >= 5, kind
        assert len(NEGATIVE[kind]) >= 3, kind


@pytest.mark.parametrize(
    ("kind", "text"), [(k, t) for k, texts in POSITIVE.items() for t in texts], ids=lambda v: str(v)[:44]
)
def test_positive_phrasings_are_detected(kind: str, text: str) -> None:
    assert detect_interrupt(text) == kind


@pytest.mark.parametrize(
    ("kind", "text"), [(k, t) for k, texts in NEGATIVE.items() for t in texts], ids=lambda v: str(v)[:44]
)
def test_negative_phrasings_do_not_trigger_their_kind(kind: str, text: str) -> None:
    assert kind not in detect_interrupts(text)


def test_ordinary_turns_trigger_nothing() -> None:
    for text in (
        "",
        "   ",
        "Yes",
        "It's Frank Alvarez, born January second nineteen eighty, loan ending 4417",
        "What's my balance?",
        "When is my next payment due?",
        "I'll pay on Friday",
        "What is the late fee on a personal loan?",
    ):
        assert detect_interrupt(text) is None, text


def test_the_spec_example_does_not_trigger_dispute() -> None:
    assert detect_interrupt("I don't want to dispute anything, just pay") is None


@pytest.mark.parametrize(
    "text",
    [
        "STOP CALLING ME",
        "Stop calling me.",
        "stop calling me!!",
        "Stop   calling\tme",
        "dont call me again",
        "Don" + chr(0x2019) + "t call me again",  # curly apostrophe from ASR
    ],
)
def test_case_punctuation_and_apostrophes_are_normalised(text: str) -> None:
    assert detect_interrupt(text) == "stop_calling"


def test_a_cease_request_with_a_window_is_not_a_cease_request() -> None:
    assert detect_interrupt("Don't call me at work") is None
    assert detect_interrupt("Do not call me during the day, call after six") is None


def test_negation_does_not_cross_a_clause_boundary() -> None:
    assert detect_interrupt("I don't want to wait, get me a human") == "human_requested"
    assert detect_interrupt("No, I want to talk to a person") == "human_requested"
    assert detect_interrupt("I'm not happy and I want to file a complaint") == "complaint"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Stop calling me, I'm going to call my lawyer", "stop_calling"),
        ("I dispute this and I want a human", "dispute"),
        ("I lost my job and I want to talk to a person", "hardship"),
        ("This is unacceptable, get me a manager", "complaint"),
        ("I'm calling my lawyer, I dispute everything", "legal"),
    ],
)
def test_priority_when_several_kinds_match(text: str, expected: str) -> None:
    assert detect_interrupt(text) == expected
    assert detect_interrupts(text)[0] == expected


def test_detect_interrupts_lists_every_kind_once_in_priority_order() -> None:
    kinds = detect_interrupts("Stop calling me. I dispute it. I want a human. I lost my job.")
    assert kinds == ["stop_calling", "dispute", "hardship", "human_requested"]
    assert len(set(kinds)) == len(kinds)


def test_every_kind_but_third_party_maps_to_an_outcome_intent() -> None:
    assert set(INTENT_FOR) == set(typing.get_args(InterruptKind)) - {"third_party"}
    assert INTENT_FOR["stop_calling"] is Intent.cease_contact
    assert INTENT_FOR["human_requested"] is Intent.human_request
    assert INTENT_FOR["dispute"] is Intent.dispute


def test_report_concern_accepts_exactly_the_interrupt_kinds_plus_misunderstanding() -> None:
    assert set(typing.get_args(base.ConcernKind)) == set(typing.get_args(InterruptKind)) | {
        "misunderstanding"
    }


# --------------------------------------------------------------------------- misunderstanding signals


@pytest.mark.parametrize(
    "text",
    [
        "I don't understand",
        "What do you mean?",
        "Huh?",
        "What?",
        "Sorry, can you repeat that?",
        "Say that again",
        "I didn't catch that",
        "I can't hear you",
        "I'm confused",
        "That doesn't make sense",
    ],
)
def test_misunderstanding_signals(text: str) -> None:
    assert is_misunderstanding(text)


@pytest.mark.parametrize(
    "text",
    [
        "I understand, go ahead",
        "What time is the payment due?",
        "It's due on the fifth",
        "Yes",
        "",
        "What is the late fee?",
    ],
)
def test_ordinary_turns_are_not_misunderstanding(text: str) -> None:
    assert not is_misunderstanding(text)


def test_detection_is_fast() -> None:
    texts = [t for texts in POSITIVE.values() for t in texts] + [
        t for texts in NEGATIVE.values() for t in texts
    ]
    detect_interrupts(texts[0])  # compile/warm
    start = time.perf_counter()
    for _ in range(20):
        for text in texts:
            detect_interrupts(text)
            is_misunderstanding(text)
    per_turn_ms = (time.perf_counter() - start) * 1000 / (20 * len(texts))
    assert per_turn_ms < 5, f"{per_turn_ms:.2f} ms per turn (agent budget is 20 ms)"

"""AGENT-R1 · the account-question and farewell detectors (pure; the shapes come from the first real call)."""

import pytest

from supportagent.agent.phrases import MAX_WORDS, is_account_question, is_farewell


@pytest.mark.parametrize(
    "text",
    [
        "I just want to know, for example, how much do I owe the bank given the times I took.",
        "What's my balance?",
        "what is my balance",
        "How much do I still owe?",
        "when is my next payment due",
        "What date is my payment due?",
        "did my payment go through",
        "What was my last payment?",
        "How much is my instalment",
        "what is the amount due",
    ],
)
def test_account_questions(text: str) -> None:
    assert is_account_question(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "what are the fees or the late fees on personal loans?",
        "What is the late fee on my personal loan?",
        "So what should I do?",
        "So what does it say for me for example, I'm already 21 days late",
        "How does paying early work?",
        "what is the grace period",
        "can you tell me what kind of files you have access to",
        "",
    ],
)
def test_policy_questions_are_not_account_questions(text: str) -> None:
    assert is_account_question(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Goodbye",
        "bye",
        "Okay, sure. Bye.",
        "bye bye",
        "That's all, thanks",
        "that is everything",
        "I'm done",
        "Nothing else, thank you",
        "have a good day",
        "no more questions",
    ],
)
def test_farewells(text: str) -> None:
    assert is_farewell(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Goodbye, what am I doing here?",  # a question: the caller is confused, not leaving
        "you were helpful.",
        "wait, don't hang up",
        "I'm not done yet",
        "I have a question about my late fee before you go, goodbye is not what I want",
        "thank you",  # alone, without "anything else?" first
        "no",
        "",
    ],
)
def test_not_farewells(text: str) -> None:
    assert is_farewell(text) is False


@pytest.mark.parametrize(
    "text", ["No.", "no thanks", "Nope", "thank you", "Thanks!", "okay thanks", "Not really"]
)
def test_a_bare_no_or_thanks_is_a_farewell_only_after_anything_else(text: str) -> None:
    assert is_farewell(text, asked_anything_else=True) is True
    assert is_farewell(text, asked_anything_else=False) is False


def test_a_long_turn_is_never_a_farewell() -> None:
    long_turn = " ".join(["well"] * MAX_WORDS) + " goodbye"
    assert len(long_turn.split()) > MAX_WORDS
    assert is_farewell(long_turn) is False


# --- the router's "does this turn say what the caller wants?" check (second real call, 2026-11-18)

from supportagent.agent.phrases import has_router_intent, is_policy_question  # noqa: E402


@pytest.mark.parametrize(
    "text",
    [
        "what are the fees or the late fees on personal loans?",
        "What's the late fee on a personal loan?",
        "Can I pay early?",
        "how long is the grace period",
        "what happens if I miss a payment",
        "what payment options do you have",
        "How do I pay my instalment online?",
        "can I pay off my loan early without a penalty",
        "what is the interest rate",
    ],
)
def test_policy_questions(text: str) -> None:
    assert is_policy_question(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Yeah, I'm going to see.",
        "Hi, how are you going to get to me, please?",
        "Hi, I need to know how you can help me",
        "hello",
        "uh-huh",
        "Okay, sure.",
        "",
    ],
)
def test_filler_and_greetings_are_not_policy_questions(text: str) -> None:
    assert is_policy_question(text) is False
    assert has_router_intent(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "What's my balance?",  # account
        "Can I pay early?",  # policy
        "I want to speak to a person",  # human
        "Can I talk to someone?",
        "I'd like to speak with a colleague",
        "I want to dispute a charge",  # an interrupt kind: handled by its own path, not "unclear"
        "stop calling me",
        "goodbye",  # a farewell
        "Tell me about my payments, I am confused",  # their own loan, but not a strict account question
        "I'm behind on my loan",
        "I think I owe something",
        "I think we are finished here then",
    ],
)
def test_turns_that_say_what_the_caller_wants_have_router_intent(text: str) -> None:
    assert has_router_intent(text) is True
